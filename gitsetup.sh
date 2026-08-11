#!/bin/bash

# Fresh init
 git init
 git branch -M main
#
# # Check what would be staged — .env must NOT appear here
 git add .
 git status
