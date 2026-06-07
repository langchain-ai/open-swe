def process_greeting(input_text):
    if input_text is None:
        raise ValueError("Input cannot be None")

    cleaned_input = input_text.strip().lower()

    if cleaned_input == "hello":
        return "Hello! How can I help you today?"

    return None
