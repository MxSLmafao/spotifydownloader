import argparse
import sys
import os
from typing import Optional
from spotify_downloader import SpotifyDownloader
from utils import parse_spotify_url
from config import SUPPORTED_FORMATS

def main():
    parser = argparse.ArgumentParser(description='Download music from Spotify links')
    parser.add_argument('url', help='Spotify URL (track or playlist)')
    parser.add_argument(
        '--format',
        choices=SUPPORTED_FORMATS,
        default='mp3',
        help='Output format (default: mp3)'
    )
    parser.add_argument(
        '--output-dir',
        help='Output directory (optional)',
        default=None
    )
    parser.add_argument(
        '--max-concurrent',
        type=int,
        help='Maximum number of concurrent downloads (optional)',
        default=None
    )
    
    args = parser.parse_args()
    
    # Get credentials from environment
    client_id = os.environ.get('SPOTIFY_CLIENT_ID')
    client_secret = os.environ.get('SPOTIFY_CLIENT_SECRET')
    
    if not client_id or not client_secret:
        print("Error: Spotify credentials not found in environment")
        sys.exit(1)
    
    try:
        # Parse URL
        content_type, content_id = parse_spotify_url(args.url)
        if not content_type or not content_id:
            print("Error: Invalid Spotify URL")
            sys.exit(1)
        
        # Initialize downloader
        downloader = SpotifyDownloader(client_id, client_secret)
        
        if content_type == 'track':
            # Single track download
            track_info = downloader.get_track_info(content_id)
            print(f"Downloading: {track_info['artist']} - {track_info['title']}")
            
            downloader.download_track(track_info, args.format, args.output_dir)
            print("Download complete!")
            
        else:  # playlist
            # Get playlist tracks
            tracks = downloader.get_playlist_tracks(content_id)
            print(f"Found {len(tracks)} tracks in playlist")
            
            # Download tracks concurrently
            successful_downloads = downloader.download_playlist_concurrent(
                tracks,
                args.format,
                args.output_dir,
                args.max_concurrent
            )
            
            print(f"\nSuccessfully downloaded {len(successful_downloads)} tracks!")
            
    except Exception as e:
        print(f"Error: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
