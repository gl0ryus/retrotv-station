"""The two-hourly writing job: systemd runs this file (retrotv-news.service) in ~/retrotv_venv. The code is in
retrotv.news."""
from retrotv.news import main

main()
