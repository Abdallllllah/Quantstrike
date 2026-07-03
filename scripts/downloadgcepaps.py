import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import time

# 1. SETUP - The main index page
BASE_URL = "https://minesec.schoolfaqs.net"
START_URL = "https://minesec.schoolfaqs.net/revision/gce-advanced-level/2022/physics"

# Folder to save the downloaded papers
DOWNLOAD_DIR = "GCE_2022_Physics_PDFs"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

def get_soup(url):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def download_file(pdf_url, filename):
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(filepath):
        print(f"Skipping (already exists): {filename}")
        return

    try:
        r = requests.get(pdf_url, stream=True)
        with open(filepath, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
        print(f"Successfully downloaded: {filename}")
    except Exception as e:
        print(f"Failed to download {filename}: {e}")

# --- MAIN LOGIC ---
print(f"🔍 Accessing revision index: {START_URL}")
main_soup = get_soup(START_URL)

if main_soup:
    # Find all links that look like paper pages (usually inside lists or table cells)
    # Adjusting for the Minesec structure: looking for links containing '/paper/'
    paper_links = main_soup.find_all('a', href=lambda href: href and "/paper/" in href)
    
    print(f"📂 Found {len(paper_links)} potential papers. Starting deep scan...")

    for link in paper_links:
        paper_page_url = urljoin(BASE_URL, link['href'])
        print(f"--- Checking paper page: {paper_page_url} ---")
        
        # Step 2: Go inside each paper page to find the actual .pdf link
        paper_soup = get_soup(paper_page_url)
        if paper_soup:
            # Find the actual PDF link (usually ends in .pdf)
            actual_pdf_link = paper_soup.find('a', href=lambda href: href and href.lower().endswith('.pdf'))
            
            if actual_pdf_link:
                pdf_url = urljoin(BASE_URL, actual_pdf_link['href'])
                # Use the link text or URL end as filename
                filename = pdf_url.split('/')[-1]
                download_file(pdf_url, filename)
                
        # Optional: be polite to the server
        time.sleep(1)

print("\n✅ Process complete!")