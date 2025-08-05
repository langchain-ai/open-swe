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

def filter_long_descriptions(input_file, output_file, min_words=100):
    with open(input_file, 'r') as f:
        prs = json.load(f)
    
    filtered_prs = []
    
    for i, pr in enumerate(prs):
        body = pr.get('body', '')
        word_count = count_words(body)
        
        if word_count > min_words:
            # Check if PR has changes in libs/langgraph/tests folder
            diff_url = pr.get('diff_url')
            if diff_url and has_langgraph_tests_changes(diff_url):
                filtered_prs.append(pr)
                print(f"Found PR #{pr.get('number')} with tests changes and {word_count} words")
        
        # Add small delay to avoid rate limiting
        if i % 10 == 0:
            time.sleep(0.1)
    
    with open(output_file, 'w') as f:
        json.dump(filtered_prs, f, indent=2)
    
    print(f"Filtered {len(filtered_prs)} PRs with descriptions longer than {min_words} words and changes in libs/langgraph/tests/ from {len(prs)} total PRs")
    return filtered_prs

if __name__ == "__main__":
    filter_long_descriptions('merged_prs.json', 'long_description_prs.json')