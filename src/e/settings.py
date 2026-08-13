from platformdirs import user_data_path

DATA_DIR = user_data_path(
    appname="e.lovinator.space",
    appauthor="TheLovinator",
    roaming=True,
    ensure_exists=True,
)
"""Directory where all the media files are stored."""
