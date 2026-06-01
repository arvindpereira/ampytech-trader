import os
import sys

EXCLUDE_DIRS = {'.git', 'node_modules', 'venv', '.next', '__pycache__', 'archive', 'saved_models', '.gemini'}
EXTENSIONS = {'.py', '.tsx', '.ts', '.css', '.md', '.json', '.yaml', '.yml'}

def clean_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        lines = content.splitlines()
        cleaned_lines = [line.rstrip() for line in lines]

        # Strip trailing empty lines
        while cleaned_lines and cleaned_lines[-1] == '':
            cleaned_lines.pop()

        new_content = '\n'.join(cleaned_lines) + '\n' if cleaned_lines else ''

        if content != new_content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Cleaned whitespace errors in: {filepath}")
            return 1
        return 0
    except Exception as e:
        print(f"Error cleaning {filepath}: {e}")
        return 0

def main():
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print(f"Scanning project root for whitespace errors: {root_dir}")

    cleaned_count = 0
    total_files = 0

    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Exclude directories
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and 'venv' not in d]

        for filename in filenames:
            ext = os.path.splitext(filename)[1]
            if ext in EXTENSIONS:
                filepath = os.path.join(dirpath, filename)
                total_files += 1
                cleaned_count += clean_file(filepath)

    print(f"Scan complete. Checked {total_files} files. Cleaned {cleaned_count} files.")

if __name__ == '__main__':
    main()
