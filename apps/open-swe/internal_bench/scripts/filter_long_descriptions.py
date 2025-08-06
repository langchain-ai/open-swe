import json
import requests
import time

def count_words(text):
    if not text:
        return 0
    return len(text.split())

def has_langgraph_tests_changes(diff_url):
    """Check if PR has changes in libs/langgraph/tests folder"""
    try:
        response = requests.get(diff_url)
        if response.status_code == 200:
            diff_content = response.text
            return 'libs/langgraph/tests/' in diff_content
        return False
    except Exception as e:
        print(f"Error fetching diff from {diff_url}: {e}")
        return False

def filter_long_descriptions(input_file, output_file, min_words=25):
    with open(input_file, 'r') as f:
        prs = json.load(f)
    
    # Load existing filtered PRs if output file exists
    existing_prs = []
    existing_pr_numbers = set()
    try:
        with open(output_file, 'r') as f:
            existing_prs = json.load(f)
            existing_pr_numbers = {pr.get('number') for pr in existing_prs}
    except FileNotFoundError:
        pass
    
    filtered_prs = existing_prs.copy()
    
    for i, pr in enumerate(prs):
        body = pr.get('body', '')
        word_count = count_words(body)
        
        if word_count > min_words:
            pr_number = pr.get('number')
            # Skip if PR is already in existing set
            if pr_number in existing_pr_numbers:
                continue
                
            # Check if PR has changes in libs/langgraph/tests folder
            diff_url = pr.get('diff_url')
            if diff_url and has_langgraph_tests_changes(diff_url):
                filtered_prs.append(pr)
                existing_pr_numbers.add(pr_number)
                print(f"Found PR #{pr_number} with tests changes and {word_count} words")
        
        # Add small delay to avoid rate limiting
        if i % 10 == 0:
            time.sleep(0.1)
    
    with open(output_file, 'w') as f:
        json.dump(filtered_prs, f, indent=2)
    
    new_prs_count = len(filtered_prs) - len(existing_prs)
    print(f"Added {new_prs_count} new PRs. Total: {len(filtered_prs)} PRs with descriptions longer than {min_words} words and changes in libs/langgraph/tests/")
    return filtered_prs

if __name__ == "__main__":
    filter_long_descriptions('merged_prs.json', 'long_description_prs.json')