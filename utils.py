import re
from typing import Tuple, Optional
from tqdm import tqdm

def parse_spotify_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse Spotify URL to get type and ID."""
    patterns = {
        'track': r'spotify.com/track/([a-zA-Z0-9]+)',
        'playlist': r'spotify.com/playlist/([a-zA-Z0-9]+)'
    }
    
    for content_type, pattern in patterns.items():
        match = re.search(pattern, url)
        if match:
            return content_type, match.group(1)
    return None, None

def create_progress_bar(total: int, desc: str) -> tqdm:
    """Create a progress bar with consistent styling."""
    return tqdm(
        total=total,
        desc=desc,
        bar_format='{l_bar}{bar:30}{r_bar}{bar:-10b}',
        unit='track'
    )

def sanitize_filename(filename: str) -> str:
    """Remove invalid characters from filename."""
    return re.sub(r'[<>:"/\\|?*]', '', filename)
