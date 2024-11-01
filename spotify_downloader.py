import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import yt_dlp
import os
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import DOWNLOAD_DIR, YTDLP_OPTIONS, FILENAME_TEMPLATE, MAX_CONCURRENT_DOWNLOADS
from utils import create_progress_bar, sanitize_filename
from audio_converter import AudioConverter
import queue
import threading
import logging

logger = logging.getLogger(__name__)

class SpotifyDownloader:
    def __init__(self, client_id: str, client_secret: str, progress_queue: Optional[queue.Queue] = None):
        """Initialize Spotify client."""
        if not client_id or not client_secret:
            raise ValueError("Spotify credentials are required")
            
        self.spotify = spotipy.Spotify(
            client_credentials_manager=SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret
            )
        )
        self.ydl = yt_dlp.YoutubeDL(YTDLP_OPTIONS)
        self.progress_queue = progress_queue
        self._active_downloads = set()
        self._lock = threading.Lock()
        
    def _check_active_download(self, track_id: str) -> bool:
        """Check if a track is already being downloaded."""
        with self._lock:
            if track_id in self._active_downloads:
                return True
            self._active_downloads.add(track_id)
            return False
            
    def _remove_active_download(self, track_id: str):
        """Remove a track from active downloads."""
        with self._lock:
            self._active_downloads.discard(track_id)
    
    def emit_progress(self, stage: str, current: int, total: int, message: str = "", extra: dict = None):
        """Emit progress event through queue if available."""
        if self.progress_queue:
            data = {
                'stage': stage,
                'current': current,
                'total': total,
                'percentage': (current / total * 100) if total > 0 else 0,
                'message': message
            }
            if extra:
                data.update(extra)
            self.progress_queue.put(data)

    def get_track_info(self, track_id: str) -> Dict:
        """Get track information from Spotify."""
        try:
            track = self.spotify.track(track_id)
            return {
                'title': track['name'],
                'artist': track['artists'][0]['name'],
                'album': track['album']['name'],
                'id': track_id
            }
        except Exception as e:
            logger.error(f"Failed to get track info: {str(e)}")
            raise Exception(f"Failed to get track info: {str(e)}")

    def get_playlist_tracks(self, playlist_id: str) -> List[Dict]:
        """Get all tracks from a playlist."""
        try:
            results = self.spotify.playlist_tracks(playlist_id)
            tracks = []
            total_tracks = results['total']
            
            self.emit_progress('fetching_playlist', 0, total_tracks, "Fetching playlist tracks")
            tracks_fetched = 0
            
            while results:
                for item in results['items']:
                    if item['track']:  # Check if track exists
                        track = item['track']
                        tracks.append({
                            'title': track['name'],
                            'artist': track['artists'][0]['name'],
                            'album': track['album']['name'],
                            'id': track['id']
                        })
                        tracks_fetched += 1
                        self.emit_progress('fetching_playlist', tracks_fetched, total_tracks)
                
                if results['next']:
                    results = self.spotify.next(results)
                else:
                    results = None
            
            return tracks
        except Exception as e:
            logger.error(f"Failed to get playlist: {str(e)}")
            raise Exception(f"Failed to get playlist: {str(e)}")

    def download_track(
        self,
        track_info: Dict,
        output_format: str,
        output_dir: Optional[str] = None
    ) -> str:
        """Download a single track."""
        if self._check_active_download(track_info['id']):
            raise Exception("Track is already being downloaded")
            
        try:
            # Create filename
            filename = sanitize_filename(
                FILENAME_TEMPLATE.format(
                    artist=track_info['artist'],
                    title=track_info['title']
                )
            )
            
            # Set output directory
            if not output_dir:
                output_dir = DOWNLOAD_DIR
            os.makedirs(output_dir, exist_ok=True)
            
            # Search query for YouTube
            query = f"{track_info['artist']} - {track_info['title']} audio"
            
            # Configure download options with progress hook
            download_opts = YTDLP_OPTIONS.copy()
            download_opts['outtmpl'] = os.path.join(output_dir, filename + '.%(ext)s')
            
            def progress_hook(d):
                if d['status'] == 'downloading':
                    total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    if total_bytes:
                        downloaded = d.get('downloaded_bytes', 0)
                        self.emit_progress('downloading', downloaded, total_bytes,
                                      f"Downloading {track_info['artist']} - {track_info['title']}",
                                      {'speed': d.get('speed', 0), 'eta': d.get('eta', 0)})
                elif d['status'] == 'finished':
                    self.emit_progress('downloading', 100, 100,
                                   f"Download complete: {track_info['artist']} - {track_info['title']}")

            download_opts['progress_hooks'] = [progress_hook]
            
            # Download the track
            with yt_dlp.YoutubeDL(download_opts) as ydl:
                ydl.download([f"ytsearch1:{query}"])
            
            # Convert to desired format if needed
            if output_format != 'wav':
                self.emit_progress('converting', 0, 100, f"Converting to {output_format}")
                input_path = os.path.join(output_dir, f"{filename}.wav")
                AudioConverter.convert_format(input_path, output_format)
                self.emit_progress('converting', 100, 100, "Conversion complete")
            
            return filename
            
        except Exception as e:
            logger.error(f"Download failed: {str(e)}")
            raise Exception(f"Download failed: {str(e)}")
        finally:
            self._remove_active_download(track_info['id'])

    def download_playlist_concurrent(
        self,
        tracks: List[Dict],
        output_format: str,
        output_dir: Optional[str] = None,
        max_workers: Optional[int] = None
    ) -> List[str]:
        """Download playlist tracks concurrently."""
        if not max_workers:
            max_workers = MAX_CONCURRENT_DOWNLOADS

        successful_downloads = []
        failed_downloads = []
        
        total_tracks = len(tracks)
        completed_tracks = 0
        
        self.emit_progress('playlist_download', 0, total_tracks, "Starting playlist download")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_track = {
                executor.submit(
                    self.download_track,
                    track,
                    output_format,
                    output_dir
                ): track for track in tracks
            }
            
            for future in as_completed(future_to_track):
                track = future_to_track[future]
                try:
                    filename = future.result()
                    successful_downloads.append(filename)
                except Exception as e:
                    failed_downloads.append({
                        'track': track,
                        'error': str(e)
                    })
                finally:
                    completed_tracks += 1
                    self.emit_progress('playlist_download', completed_tracks, total_tracks,
                                   f"Completed {completed_tracks}/{total_tracks} tracks")
        
        # Report failed downloads
        if failed_downloads:
            failure_message = "\nFailed downloads:\n" + "\n".join(
                f"- {fail['track']['artist']} - {fail['track']['title']}: {fail['error']}"
                for fail in failed_downloads
            )
            self.emit_progress('playlist_download', total_tracks, total_tracks, failure_message)
        
        return successful_downloads
