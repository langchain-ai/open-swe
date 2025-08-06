import json
import os
import webbrowser
import time

def update_final_set():
    # Read the long_description_prs.json file
    with open('long_description_prs.json', 'r') as file:
        data = json.load(file)
    
    # Load existing final_set.json if it exists
    final_set = []
    final_set_numbers = set()
    try:
        with open('final_set.json', 'r') as file:
            final_set = json.load(file)
            final_set_numbers = {pr.get('number') for pr in final_set}
    except FileNotFoundError:
        pass
    
    # Filter PRs with is_good_pr = true and add to final_set if not already there
    added_count = 0
    for pr in data:
        if pr.get('is_good_pr') == True:
            pr_number = pr.get('number')
            if pr_number not in final_set_numbers:
                final_set.append(pr)
                final_set_numbers.add(pr_number)
                added_count += 1
                print(f"Added PR #{pr_number} to final set")
    
    # Write back to final_set.json
    with open('final_set.json', 'w') as file:
        json.dump(final_set, file, indent=2)
    
    print(f"Added {added_count} new PRs. Total PRs in final set: {len(final_set)}")

if __name__ == "__main__":
    update_final_set()   