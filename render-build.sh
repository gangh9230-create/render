#!/usr/bin/env bash
set -o errexit

echo "Installing Chrome..."

apt-get update
apt-get install -y wget unzip curl gnupg

# Chrome 설치
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google.list
apt-get update
apt-get install -y google-chrome-stable

echo "Installing ChromeDriver..."

CHROME_VERSION=$(google-chrome --version | awk '{print $3}' | cut -d '.' -f 1)
DRIVER_VERSION=$(curl -s "https://chromedriver.storage.googleapis.com/LATEST_RELEASE_$CHROME_VERSION")

wget -q https://chromedriver.storage.googleapis.com/$DRIVER_VERSION/chromedriver_linux64.zip
unzip chromedriver_linux64.zip
mv chromedriver /usr/bin/chromedriver
chmod +x /usr/bin/chromedriver

echo "Build complete"
