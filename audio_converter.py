from pydub import AudioSegment
import os
from typing import Optional

class AudioConverter:
    @staticmethod
    def convert_format(
        input_path: str,
        output_format: str,
        output_path: Optional[str] = None
    ) -> str:
        """Convert audio file to specified format."""
        try:
            # Load the audio file
            audio = AudioSegment.from_file(input_path)
            
            # If no output path specified, replace extension of input path
            if not output_path:
                output_path = os.path.splitext(input_path)[0] + '.' + output_format
            
            # Export in the desired format
            audio.export(output_path, format=output_format)
            
            # Remove original file if output path is different
            if output_path != input_path:
                os.remove(input_path)
            
            return output_path
        except Exception as e:
            raise Exception(f"Conversion failed: {str(e)}")
