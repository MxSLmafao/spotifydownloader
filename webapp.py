from flask import Flask, render_template, request, jsonify, send_file, Response
import os
import zipfile
from spotify_downloader import SpotifyDownloader
from utils import parse_spotify_url
from config import SUPPORTED_FORMATS, DOWNLOAD_DIR
import json
import queue
import threading
from datetime import datetime, timedelta
import logging
import shutil
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Global progress queue for SSE updates
progress_queues = {}
# Global downloads tracking
current_downloads = {}
# Global cancellation tracking
cancel_flags = {}
# Global download threads
download_threads = {}
# Global connection tracking
connection_status = {}
# Global connection timestamps
connection_timestamps = {}
# Maximum connection age (30 minutes)
MAX_CONNECTION_AGE = timedelta(minutes=30)
# Maximum retry attempts for SSE
MAX_RETRY_ATTEMPTS = 5
# Base delay for exponential backoff (in seconds)
BASE_RETRY_DELAY = 1
# Maximum age for downloaded files (24 hours)
MAX_FILE_AGE = timedelta(hours=24)
# Cleanup interval (1 hour)
CLEANUP_INTERVAL = timedelta(hours=1)

def init_app():
    """Initialize the application."""
    # Clear downloads directory on startup
    cleanup_downloads(force=True)
    # Schedule periodic cleanup
    schedule_cleanup()

def schedule_cleanup():
    """Schedule periodic cleanup of downloads directory."""
    def cleanup_task():
        while True:
            cleanup_downloads()
            threading.Event().wait(CLEANUP_INTERVAL.total_seconds())
    
    cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
    cleanup_thread.start()

def cleanup_downloads(force=False):
    """Clean up old downloads."""
    try:
        current_time = datetime.now()
        if os.path.exists(DOWNLOAD_DIR):
            # If force is True, remove everything
            if force:
                shutil.rmtree(DOWNLOAD_DIR)
                os.makedirs(DOWNLOAD_DIR)
                logger.info("Downloads directory cleared")
                return

            # Remove old files and empty directories
            for root, dirs, files in os.walk(DOWNLOAD_DIR, topdown=False):
                # Remove old files
                for file in files:
                    file_path = os.path.join(root, file)
                    file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                    if current_time - file_time > MAX_FILE_AGE:
                        try:
                            os.remove(file_path)
                            logger.debug(f"Removed old file: {file_path}")
                        except Exception as e:
                            logger.error(f"Error removing file {file_path}: {str(e)}")
                
                # Remove empty directories
                for dir in dirs:
                    dir_path = os.path.join(root, dir)
                    try:
                        if not os.listdir(dir_path):
                            os.rmdir(dir_path)
                            logger.debug(f"Removed empty directory: {dir_path}")
                    except Exception as e:
                        logger.error(f"Error removing directory {dir_path}: {str(e)}")
        
        # Ensure downloads directory exists
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        
    except Exception as e:
        logger.error(f"Error during cleanup: {str(e)}")

def cleanup_stale_connections():
    """Clean up stale SSE connections."""
    current_time = datetime.now()
    stale_connections = []
    
    for queue_id, timestamp in connection_timestamps.items():
        if current_time - timestamp > MAX_CONNECTION_AGE:
            stale_connections.append(queue_id)
    
    for queue_id in stale_connections:
        logger.debug(f"Cleaning up stale connection: {queue_id}")
        cleanup_download(queue_id)

def get_spotify_client():
    """Get authenticated Spotify client."""
    client_id = os.environ.get('SPOTIFY_CLIENT_ID')
    client_secret = os.environ.get('SPOTIFY_CLIENT_SECRET')
    
    if not client_id or not client_secret:
        raise ValueError("Spotify credentials not configured")
        
    return spotipy.Spotify(
        client_credentials_manager=SpotifyClientCredentials(
            client_id=client_id,
            client_secret=client_secret
        )
    )

@app.route('/search')
def search_tracks():
    """Search for tracks on Spotify."""
    try:
        query = request.args.get('q')
        offset = int(request.args.get('offset', 0))
        limit = int(request.args.get('limit', 5))

        if not query:
            return jsonify({'error': 'Query parameter is required'}), 400

        sp = get_spotify_client()
        results = sp.search(q=query, type='track', limit=limit, offset=offset)
        
        if not results or 'tracks' not in results:
            return jsonify({'error': 'No results found'}), 404

        tracks = []
        for track in results['tracks']['items']:
            tracks.append({
                'id': track['id'],
                'title': track['name'],
                'artist': track['artists'][0]['name'],
                'album': track['album']['name'],
                'duration': track['duration_ms'] // 1000,
                'url': track['external_urls']['spotify'],
                'image': track['album']['images'][0]['url'] if track['album']['images'] else None
            })

        return jsonify({
            'tracks': tracks,
            'total': results['tracks']['total'],
            'offset': offset,
            'limit': limit
        })

    except Exception as e:
        logger.error(f"Error searching tracks: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/')
def index():
    return render_template('index.html', formats=SUPPORTED_FORMATS)

def cleanup_download(queue_id):
    """Clean up resources for a download."""
    try:
        # Remove from global tracking
        progress_queues.pop(queue_id, None)
        connection_status.pop(queue_id, None)
        connection_timestamps.pop(queue_id, None)
        cancel_flags.pop(queue_id, None)
        
        # Stop any running threads
        thread = download_threads.pop(queue_id, None)
        if thread and thread.is_alive():
            cancel_flags[queue_id] = True
            thread.join(timeout=5)
        
        # Clean up download directory
        download_path = os.path.join(DOWNLOAD_DIR, queue_id)
        if os.path.exists(download_path):
            shutil.rmtree(download_path)
            os.makedirs(download_path, exist_ok=True)
            
        logger.debug(f"Cleanup completed for queue {queue_id}")
    except Exception as e:
        logger.error(f"Error during cleanup for {queue_id}: {str(e)}")

@app.route('/cancel', methods=['POST'])
def cancel_download():
    """Cancel ongoing downloads."""
    try:
        for queue_id in list(progress_queues.keys()):
            cancel_flags[queue_id] = True
            cleanup_download(queue_id)
        return jsonify({'status': 'cancelled'})
    except Exception as e:
        logger.error(f"Error cancelling downloads: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download', methods=['POST'])
def download():
    """Handle download requests."""
    try:
        # Validate input
        spotify_url = request.form.get('spotify_url')
        output_format = request.form.get('format')
        
        if not spotify_url or not output_format:
            return jsonify({'error': 'Missing required parameters'}), 400
        
        if output_format not in SUPPORTED_FORMATS:
            return jsonify({'error': 'Unsupported format'}), 400
        
        content_type, content_id = parse_spotify_url(spotify_url)
        if not content_type or not content_id:
            return jsonify({'error': 'Invalid Spotify URL'}), 400
        
        # Check for existing download
        queue_id = f"{content_type}_{content_id}"
        if queue_id in progress_queues:
            logger.warning(f"Existing download found for {queue_id}")
            return jsonify({'error': 'Download already in progress'}), 409
        
        # Initialize download resources
        progress_queue = queue.Queue()
        progress_queues[queue_id] = progress_queue
        cancel_flags[queue_id] = False
        connection_status[queue_id] = 'initializing'
        connection_timestamps[queue_id] = datetime.now()
        
        # Create download directory
        download_path = os.path.join(DOWNLOAD_DIR, queue_id)
        os.makedirs(download_path, exist_ok=True)
        
        # Initialize downloader
        client_id = os.environ.get('SPOTIFY_CLIENT_ID')
        client_secret = os.environ.get('SPOTIFY_CLIENT_SECRET')
        
        if not client_id or not client_secret:
            return jsonify({'error': 'Spotify credentials not configured'}), 500
            
        downloader = SpotifyDownloader(client_id, client_secret, progress_queue)
        
        def download_task():
            try:
                if content_type == 'track':
                    track_info = downloader.get_track_info(content_id)
                    if not cancel_flags.get(queue_id):
                        filename = downloader.download_track(
                            track_info,
                            output_format,
                            download_path
                        )
                        progress_queue.put({
                            'type': 'complete',
                            'successful_downloads': 1,
                            'message': f'Successfully downloaded: {track_info["artist"]} - {track_info["title"]}'
                        })
                else:  # playlist
                    tracks = downloader.get_playlist_tracks(content_id)
                    if not cancel_flags.get(queue_id):
                        successful_downloads = downloader.download_playlist_concurrent(
                            tracks,
                            output_format,
                            download_path
                        )
                        if successful_downloads:
                            try:
                                zip_path = create_zip_file(tracks, output_format, queue_id)
                                current_downloads['latest_zip'] = zip_path
                                progress_queue.put({
                                    'type': 'complete',
                                    'successful_downloads': len(successful_downloads),
                                    'message': f'Successfully downloaded {len(successful_downloads)} tracks'
                                })
                            except Exception as e:
                                raise Exception(f"Error creating ZIP file: {str(e)}")
            except Exception as e:
                logger.error(f"Error in download task: {str(e)}")
                progress_queue.put({
                    'type': 'error',
                    'message': str(e)
                })
            finally:
                progress_queue.put(None)
                cleanup_download(queue_id)
        
        # Start download in background
        download_thread = threading.Thread(target=download_task, daemon=True)
        download_thread.start()
        download_threads[queue_id] = download_thread
        
        return jsonify({'queue_id': queue_id})
        
    except Exception as e:
        logger.error(f"Error initiating download: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/progress/<queue_id>')
def progress(queue_id):
    """SSE endpoint for progress updates."""
    try:
        return Response(
            progress_event_stream(queue_id),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no'
            }
        )
    except Exception as e:
        logger.error(f"Error in progress stream: {str(e)}")
        return jsonify({'error': str(e)}), 500

def progress_event_stream(queue_id):
    """Generate SSE events for progress updates."""
    logger.debug(f"Starting SSE stream for queue_id: {queue_id}")
    
    if queue_id not in progress_queues:
        yield f"data: {json.dumps({'type': 'error', 'message': 'Invalid queue ID'})}\n\n"
        return
        
    try:
        progress_queue = progress_queues[queue_id]
        connection_status[queue_id] = 'connected'
        connection_timestamps[queue_id] = datetime.now()
        
        yield f"data: {json.dumps({'type': 'connected', 'queue_id': queue_id})}\n\n"
        
        while True:
            try:
                if cancel_flags.get(queue_id):
                    yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
                    break
                    
                if datetime.now() - connection_timestamps[queue_id] > MAX_CONNECTION_AGE:
                    yield f"data: {json.dumps({'type': 'timeout'})}\n\n"
                    break
                    
                data = progress_queue.get(timeout=30)
                if data is None:
                    break
                    
                yield f"data: {json.dumps(data)}\n\n"
                
            except queue.Empty:
                continue
                
    except GeneratorExit:
        logger.debug(f"Client disconnected from queue {queue_id}")
    finally:
        cleanup_download(queue_id)

# Initialize app on startup
init_app()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
