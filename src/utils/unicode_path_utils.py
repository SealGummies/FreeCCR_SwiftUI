"""
Utility functions for handling Unicode file paths across different platforms.
"""
import os
import sys
import logging


def normalize_unicode_path(file_path: str) -> str:
    """
    Normalize a file path to handle Unicode characters properly across platforms.
    
    Args:
        file_path (str): The input file path
        
    Returns:
        str: The normalized file path
    """
    try:
        # Normalize path separators and resolve relative paths
        normalized = os.path.normpath(os.path.abspath(file_path))
        
        # On Windows, ensure proper Unicode handling
        if sys.platform == "win32":
            # Convert to absolute path and normalize
            normalized = os.path.abspath(normalized)
            
        return normalized
    except Exception as e:
        logging.warning(f"Error normalizing Unicode path '{file_path}': {e}")
        return file_path


def validate_unicode_path(file_path: str) -> bool:
    """
    Validate that a file path can be properly handled with Unicode characters.
    
    Args:
        file_path (str): The file path to validate
        
    Returns:
        bool: True if the path is valid and accessible, False otherwise
    """
    try:
        normalized_path = normalize_unicode_path(file_path)
        
        # Check if path exists and is accessible
        if os.path.exists(normalized_path):
            # Try to get file stats to ensure we can access it
            os.stat(normalized_path)
            return True
        else:
            # For non-existent paths (like export targets), check if parent directory exists
            parent_dir = os.path.dirname(normalized_path)
            if os.path.exists(parent_dir) and os.access(parent_dir, os.W_OK):
                return True
            
        return False
        
    except (OSError, UnicodeError, ValueError) as e:
        logging.warning(f"Unicode path validation failed for '{file_path}': {e}")
        return False


def get_safe_filename(filename: str) -> str:
    """
    Convert a filename to a safe version that avoids Unicode issues.
    
    Args:
        filename (str): The original filename
        
    Returns:
        str: A safe filename
    """
    try:
        # Remove or replace problematic characters
        import re
        
        # Keep alphanumeric, spaces, dots, hyphens, underscores, and some Unicode
        safe_filename = re.sub(r'[^\w\s\-_.\u00A0-\uFFFF]', '_', filename)
        
        # Ensure it's not too long
        if len(safe_filename) > 200:
            name, ext = os.path.splitext(safe_filename)
            safe_filename = name[:200-len(ext)] + ext
            
        return safe_filename
        
    except Exception as e:
        logging.warning(f"Error creating safe filename for '{filename}': {e}")
        # Fallback to basic ASCII-safe name
        import uuid
        return f"image_{uuid.uuid4().hex[:8]}.tiff"


def check_unicode_support() -> bool:
    """
    Check if the current system properly supports Unicode file paths.
    
    Returns:
        bool: True if Unicode is fully supported, False otherwise
    """
    try:
        import tempfile
        
        # Create a temporary file with Unicode characters
        test_unicode_name = "测试_test_テスト.txt"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            test_path = os.path.join(temp_dir, test_unicode_name)
            
            # Try to create and access the file
            with open(test_path, 'w', encoding='utf-8') as f:
                f.write("Unicode test")
            
            # Try to read it back
            with open(test_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Check if we can list the directory
            files = os.listdir(temp_dir)
            unicode_file_found = any(test_unicode_name in fname for fname in files)
            
            return unicode_file_found and content == "Unicode test"
            
    except Exception as e:
        logging.warning(f"Unicode support check failed: {e}")
        return False
