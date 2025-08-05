#!/usr/bin/env python3
import requests
import json
import sys
import os
from dotenv import load_dotenv

load_dotenv()

def fetch_merged_prs():
    """
    Fetch all merged PRs from langchain-ai/langgraph repository
    """
    base_url = "https://api.github.com/repos/langchain-ai/langgraph"
    all_prs = []
    page = 1
    per_page = 100
    
    # Get GitHub token from environment
    github_token = os.getenv('GITHUB_TOKEN')
    
    headers = {
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'PR-Fetcher'
    }
    
    if github_token:
        headers['Authorization'] = f'token {github_token}'
        print("Using GitHub token for authentication")
    else:
        print("No GitHub token found - using unauthenticated requests (rate limited)")
    
    try:
        while True:
            url = f"{base_url}/pulls?state=closed&per_page={per_page}&page={page}"
            
            print(f"Fetching page {page}...")
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            
            if not data:
                break
            
            # Filter for merged PRs only
            merged_prs = [pr for pr in data if pr.get('merged_at') is not None]
            all_prs.extend(merged_prs)
            print(f"Fetched {len(merged_prs)} merged PRs from {len(data)} total PRs on page {page} (total merged: {len(all_prs)})")
            
            # If we got less than per_page results, we're done
            if len(data) < per_page:
                break
                
            page += 1
        
        print(f"Total merged PRs fetched: {len(all_prs)}")
        
        # Save to JSON file
        output_file = "merged_prs.json"
        with open(output_file, 'w') as f:
            json.dump(all_prs, f, indent=2)
        
        print(f"Saved all merged PRs to {output_file}")
        return all_prs
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching PR data: {e}")
        return None

if __name__ == "__main__":
    fetch_merged_prs()