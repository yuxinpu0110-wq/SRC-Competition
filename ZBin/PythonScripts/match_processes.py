"""Startup checks for accidental duplicate simulation / Client processes."""
import csv
import io
import subprocess


def process_count(name):
    result = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {name}', '/FO', 'CSV', '/NH'],
                            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(f'Cannot verify {name} ownership: tasklist returned {result.returncode}')
    rows = csv.reader(io.StringIO(result.stdout.decode('mbcs', errors='replace')))
    return sum(bool(row) and row[0].lower() == name.lower() for row in rows)


def check_duplicates():
    for name in ('Client.exe', 'grSim.exe'):
        count = process_count(name)
        if count > 1:
            raise RuntimeError(f'{count} copies of {name} are running. Keep ONE instance; duplicate vision invalidates strategy/testing.')
