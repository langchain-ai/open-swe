#!/usr/bin/env python3
"""
Script to process final_set.json and extract relevant information for coding evaluations.

For each PR in the dataset, extracts:
- URL
- merge_commit_sha  
- body
- title
"""

import json
import re
from typing import Dict, Any

def extract_repo_from_url(url: str) -> tuple[str, str]:
    """Extract owner and repo name from GitHub API URL."""
    match = re.search(r'/repos/([^/]+)/([^/]+)/', url)
    if match:
        return match.group(1), match.group(2)
    raise ValueError(f"Could not extract repo info from URL: {url}")

def process_pr(pr_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process a single PR entry and extract relevant information."""
    try:
        owner, repo = extract_repo_from_url(pr_data['url'])
        
        return {
            'url': pr_data['url'],
            'html_url': pr_data['html_url'],
            'diff_url': pr_data['diff_url'],
            'patch_url': pr_data['patch_url'],
            'repo_owner': owner,
            'repo_name': repo,
            'pr_number': pr_data['number'],
            'merge_commit_sha': pr_data['merge_commit_sha'],
            'title': pr_data['title'],
            'body': pr_data['body'],
            'created_at': pr_data['created_at'],
            'merged_at': pr_data['merged_at']
        }
    except Exception as e:
        print(f"Error processing PR {pr_data.get('number', 'unknown')}: {e}")
        return None

def main():
    with open('final_set.json', 'r') as f:
        pr_data_list = json.load(f)
    
    print(f"Processing {len(pr_data_list)} PRs...")
    
    processed_prs = []
    
    for i, pr_data in enumerate(pr_data_list):
        print(f"Processing PR {i+1}/{len(pr_data_list)}: {pr_data.get('number', 'unknown')}")
        
        processed_pr = process_pr(pr_data)
        if processed_pr:
            processed_prs.append(processed_pr)
    
    output_file = 'processed_prs.json'
    with open(output_file, 'w') as f:
        json.dump(processed_prs, f, indent=2)
    
    print(f"\nProcessed {len(processed_prs)} PRs successfully.")
    print(f"Output saved to {output_file}")

if __name__ == "__main__":
    main()