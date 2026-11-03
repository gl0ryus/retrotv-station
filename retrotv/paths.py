"""
Where the station's files are: the Pi's SD card, the NAS, and the station folder itself. Nothing happens on import,
so the writing job (retrotv.news, in its own venv) uses it as well as the station.
"""
import os

HOME_DIR = "/home/gl0ryus"
STATION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the station folder on the NAS
NAS_ROOT = "/mnt/tower/.retrotv"
MEDIA_ROOT = os.path.join(NAS_ROOT, "videos")
ASSETS_DIR = os.path.join(HOME_DIR, "retrotv_assets")
DATA_DIR = os.path.join(HOME_DIR, "retrotv_data")
MANIFEST_FILE = os.path.join(HOME_DIR, "retrotv_manifest.json")
INDEX_FILE = os.path.join(HOME_DIR, "retrotv_index.json")
SCHEDULE_FILE = os.path.join(HOME_DIR, "retrotv_schedule.json")
LOCATION_FILE = os.path.join(STATION_DIR, "location.json")   # kept out of git: see .gitignore
