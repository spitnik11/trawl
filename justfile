# trawl tasks — Python single-file app.
set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# show the task list
default:
    @just --list

# run the app (serves the combined UI, localhost:8420)
run:
    python trawl.py

# one-time Reddit OAuth / credential setup
setup:
    python trawl.py setup
