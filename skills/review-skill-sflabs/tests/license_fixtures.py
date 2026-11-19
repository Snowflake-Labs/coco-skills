"""Full public repository LICENSE fixtures; never execute a skill or fetch a URL."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
APACHE_PATH = 'skills/dbt-model-generator/LICENSE'
SNOWFLAKE_PATH = 'skills/review-skill-sflabs/LICENSE'
APACHE = (ROOT / APACHE_PATH).read_bytes()
SNOWFLAKE = (ROOT / SNOWFLAKE_PATH).read_bytes()
