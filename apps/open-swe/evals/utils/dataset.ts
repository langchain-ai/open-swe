interface DatasetInput {
  repo: string;
  branch: string;
  user_input: string;
  test_input: string;
  ground_truth: string;
}

interface DatasetItem {
  inputs: DatasetInput;
}

export const DATASET: DatasetItem[] = [
  {
    inputs: {
      repo: "mai-sandbox/open-swe_write_ReAct_eval",
      branch: "main",
      user_input: `
Hey, we need a basic chat assistant for our project. Nothing too crazy, just something that can chat with users and handle a couple of simple tasks.

We want users to be able to:
  - Have normal conversations with the assistant
  - Ask it to search for stuff online when they need current info
  - Get help with basic math calculations

The assistant should be smart enough to know when to use tools vs just chat normally.

Requirements:
  - Use LangGraph for the main workflow (we're standardizing on this)
  - Anthropic Claude for the LLM (we have API keys already)
  - Keep it simple - this is a proof of concept

Tools to implement:
  1. Search tool — for when users ask about current events, facts, etc.
  2. Calculator tool — for when they need math help

The agent should be able to converse with the user, search for info using the web tool, and use the calculator tool for arithmetic.

Other notes:
  - Make sure it actually compiles and runs without errors
  - Add type hints (our code standards require them)
  - Handle errors gracefully — tools might fail sometimes
  - Don't overthink the routing logic, simple is fine

The goal is to have a working assistant that demonstrates LangGraph basics. We'll probably extend it later with more features.
      `.trim(),
      test_input:
        "Search the web for the date when the Wells Fargo Center in Philadelphia first opened to the public, then calculate how many full years it has been open as of today (July 14, 2025), and finally summarize that in one sentence",
      ground_truth:
        "The Wells Fargo Center in Philadelphia first opened to the public on August 31, 1996, meaning it has been open for 28 full years as of July 14, 2025",
    },
  },
  {
    inputs: {
      repo: "mai-sandbox/open-swe_write_React_weather",
      branch: "main",
      user_input: `
Hey, we need a really basic chatbot that can check the weather.

What we want:
  - Users should be able to chat normally with the bot
  - Ask about weather in any city
  - Get helpful weather information when they need it

That's it! Keep it as simple as possible.

Tool:
  - Weather tool — looks up current weather for cities

The agent should be able to talk to user and answer any question related to weather using the weather tool.

Implementation notes:
  - The bot should automatically detect weather questions
  - Use the weather tool when needed, chat normally otherwise
  - Keep the routing logic simple — if they mention a city + weather, use the tool
  - Make sure it compiles and runs without errors
      `.trim(),
      test_input: "What's the weather in London right now?",
      ground_truth:
        "The weather in London is cloudy with a chance of rain, and about 58°F.",
    },
  },
  {
    inputs: {
      repo: "mai-sandbox/open-swe_ticket-Manager",
      branch: "main",
      user_input: `
Hey, we need an advanced Support Ticket Triage agent built on LangGraph.

What we want:
  • Support staff will feed in raw customer tickets (text).
  • The agent should perform these steps in order:

    1. Classify the ticket into one of three buckets: "Billing", "Technical", or "General Inquiry".
    2. Detect Priority as "Low", "Medium", or "High" based on urgency clues.
    3. Summarize the ticket in one clear sentence.
    4. Route it to the correct email, using these rules:
       - Billing + High → priority-billing@company.com
       - Billing + else → billing@company.com
       - Technical + High → urgent-tech@company.com
       - Technical + else → tech@company.com
       - General Inquiry → support@company.com
    5. Draft an Acknowledgement email snippet (1–2 sentences) referencing the summary and routing.

Key features:
  1. Classification Node — LLM reads \`ticket_text\` → sets \`category\`.
      `.trim(),
      test_input:
        "My internet connection drops every few hours—please help troubleshoot.",
      ground_truth:
        '{"category":"Technical","priority":"Medium","summary":"Customer\'s internet connection intermittently drops and they need troubleshooting.","route_to":"tech@company.com","ack_draft":"Thanks for reporting your connectivity issue. I\'ve routed your ticket to our technical team, and they\'ll work with you to resolve it."}',
    },
  },
  {
    inputs: {
      repo: "mai-sandbox/open-swe_news_trend_agent",
      branch: "main",
      user_input: `
Hey, we need a Multi-Agent News-Trend Alert System built on LangGraph.

What we want:
  • Input: a topic string, e.g. "electric vehicles."
  • The system invokes three agents in sequence — and each node returns its result as a string:

    1. Fetcher Agent (\`fetcher\`)
       - Takes \`state["topic"]\` and uses a news-fetch tool to get the top 5 headlines.
       - Returns the headlines list serialized as a JSON string, and writes it into \`state["headlines_str"]\`.

    2. Analyzer Agent (\`analyzer\`)
       - Reads \`state["headlines_str"]\` (a JSON string), parses it, then finds words appearing ≥2 times.
       - Returns the trends list serialized as a JSON string, and writes it into \`state["trends_str"]\`.

    3. Reporter Agent (\`reporter\`)
       - Reads \`state["trends_str"]\` and \`state["topic"]\`.
       - If trends exist, creates this object:
         {
           "trend_found": true,
           "trend_keywords": [...],
           "alert_summary": "In the latest headlines on {topic}, we saw repeated mentions of {keywords}."
         }
       - Otherwise:
         {
           "trend_found": false,
           "alert_summary": "No new trend detected for {topic}."
         }
       - Returns that entire object serialized as a single JSON string.

Key features:
  - State TypedDict:
    class State(TypedDict):
        topic: str
        headlines_str: str  # JSON string of List[str]
        trends_str:    str  # JSON string of List[str]
      `.trim(),
      test_input: "Oscars 2025 winners",
      ground_truth:
        '{ "trend_found": true, "trend_keywords": ["Oppenheimer", "Barbie"], "alert_summary": "In the latest headlines on Oscars 2025 winners, we saw repeated mentions of Oppenheimer and Barbie." }',
    },
  },
  {
    inputs: {
      repo: "mai-sandbox/open-swe_finance_tracker",
      branch: "main",
      user_input: `
Hey, we need a Personal Finance Tracker agent built on LangGraph.

Requirements:
  1. The compiled graph's \`.invoke()\` must return \`state["summary_str"]\` as a JSON string.
  2. All node functions return dictionaries that update the state.
  3. The graph should start with this exact default state:
     transactions: '[{"date": "2024-01-05", "description": "Whole Foods Market", "amount": 125.50}, {"date": "2024-01-12", "description": "Safeway Grocery", "amount": 99.50}, {"date": "2024-01-01", "description": "Monthly Rent Payment", "amount": 1005.00}, {"date": "2024-01-15", "description": "PG&E Electric Bill", "amount": 85.00}, {"date": "2024-01-20", "description": "Water Utility", "amount": 65.00}, {"date": "2024-01-08", "description": "Netflix Subscription", "amount": 15.99}, {"date": "2024-01-14", "description": "Movie Theater Tickets", "amount": 45.00}, {"date": "2024-01-22", "description": "Concert Tickets", "amount": 89.01}]'
     category_budget: '{"Groceries":200,"Rent":1000,"Utilities":150,"Entertainment":100}'
     categorized_str: ""
     summary_str: ""

Workflow (three nodes in sequence):

  1. Categorizer Node (\`categorizer\`)
     - Input: \`state["transactions"]\` (JSON string of transaction list)
     - Action: Use LLM to assign each transaction a "category" field
     - Category Rules: Map to exactly these categories: "Groceries", "Rent", "Utilities", "Entertainment", "Other"
     - LLM Prompt Template:
       Categorize these transactions into: Groceries, Rent, Utilities, Entertainment, or Other.
       Return valid JSON with original fields plus 'category' field.
       Transactions: {transactions_json}
     - Return: {"categorized_str": "[{...transactions with category field...}]"}
     - Error Handling: If JSON parsing fails, return original transactions string

  2. Summarizer Node (\`summarizer\`)
     - Input: \`state["categorized_str"]\`
     - Action: Parse JSON and sum amounts per category
     - Return: {"summary_str": '{"Groceries": 225.0, "Rent": 1005.0, ...}'}
     - Error Handling: If parsing fails, return {"summary_str": "{}"}

  3. Advisor Node (\`advisor\`)
     - Input: \`state["summary_str"]\` and \`state["category_budget"]\`
     - Action: Compare spending vs budget, generate advice ONLY for over-budget categories
     - Advice Logic:
         - If spent > budget: Generate LLM advice
         - If spent <= budget: No advice for that category
         - If no overspending: advice should be an empty object (advice: {}).
     - LLM Prompt Template:
       For each category where spending exceeds the budget, generate advice using this template:
       "You overspent $<overage> in <category> (budget: $<budget>, spent: $<spent>). Provide one practical tip in 1-2 sentences to reduce spending."
     - Final Return: Return a JSON string with both the category summary and advice, e.g.:
       {"category_summary": {...}, "advice": {...}}
     - Error Handling: If parsing fails, return a summary with an empty advice object.

Output Format:
  The final .invoke() return value must be a JSON string exactly like:
  {
    "category_summary": {
      "Groceries": 225.0,
      "Rent": 1005.0,
      "Utilities": 150.0,
      "Entertainment": 150.0
    },
    "advice": {
      "Groceries": "Consider meal planning and buying generic brands.",
      "Rent": "Look for ways to offset the slight overage.",
      "Entertainment": "Set a stricter entertainment budget."
    }
  }

Implementation Notes:
  - Use init_chat_model("anthropic:claude-3-5-sonnet-latest") for LLM calls
  - All state values are JSON strings, parse with json.loads()
  - Graph flow: START → categorizer → summarizer → advisor → END
  - Export as compiled_graph = graph_builder.compile()
      `.trim(),
      test_input: "Show me my monthly spending overview",
      ground_truth:
        '{"category_summary": {"Groceries": 225.0, "Rent": 1005.0, "Utilities": 150.0, "Entertainment": 150.0}, "advice": {"Groceries": "Consider meal planning and buying generic brands to reduce grocery costs.", "Rent": "Look for ways to reduce utilities or consider a roommate to offset the slight rent overage.", "Entertainment": "Set a stricter entertainment budget and look for free or low-cost activities."}}',
    },
  },
  {
    inputs: {
      repo: "mai-sandbox/open-swe_finance_tracker",
      branch: "main",
      user_input: `
Hey, we need a Personal Finance Tracker agent built on LangGraph.

Requirements:
  1. The compiled graph's \`.invoke()\` must return \`state["summary_str"]\` as a JSON string.
  2. All node functions return dictionaries that update the state.
  3. The graph should start with this exact default state:
     transactions: '[{"date": "2024-01-05", "description": "Whole Foods Market", "amount": 125.50}, {"date": "2024-01-12", "description": "Safeway Grocery", "amount": 99.50}, {"date": "2024-01-01", "description": "Monthly Rent Payment", "amount": 1005.00}, {"date": "2024-01-15", "description": "PG&E Electric Bill", "amount": 85.00}, {"date": "2024-01-20", "description": "Water Utility", "amount": 65.00}, {"date": "2024-01-08", "description": "Netflix Subscription", "amount": 15.99}, {"date": "2024-01-14", "description": "Movie Theater Tickets", "amount": 45.00}, {"date": "2024-01-22", "description": "Concert Tickets", "amount": 89.01}]'
     category_budget: '{"Groceries":200,"Rent":1000,"Utilities":150,"Entertainment":100}'
     categorized_str: ""
     summary_str: ""

Workflow (three nodes in sequence):

  1. Categorizer Node (\`categorizer\`)
     - Input: \`state["transactions"]\` (JSON string of transaction list)
     - Action: Use LLM to assign each transaction a "category" field
     - Category Rules: Map to exactly these categories: "Groceries", "Rent", "Utilities", "Entertainment", "Other"
     - LLM Prompt Template:
       Categorize these transactions into: Groceries, Rent, Utilities, Entertainment, or Other.
       Return valid JSON with original fields plus 'category' field.
       Transactions: {transactions_json}
     - Return: {"categorized_str": "[{...transactions with category field...}]"}
     - Error Handling: If JSON parsing fails, return original transactions string

  2. Summarizer Node (\`summarizer\`)
     - Input: \`state["categorized_str"]\`
     - Action: Parse JSON and sum amounts per category
     - Return: {"summary_str": '{"Groceries": 225.0, "Rent": 1005.0, ...}'}
     - Error Handling: If parsing fails, return {"summary_str": "{}"}

  3. Advisor Node (\`advisor\`)
     - Input: \`state["summary_str"]\` and \`state["category_budget"]\`
     - Action: Compare spending vs budget, generate advice ONLY for over-budget categories
     - Advice Logic:
         - If spent > budget: Generate LLM advice
         - If spent <= budget: No advice for that category
         - If no overspending: "advice": {}
     - LLM Prompt Template:
       You overspent <overage> in <category> (budget: <budget>, spent: <spent>).
       Provide one practical tip in 1-2 sentences to reduce spending.
     - Final Return: {"summary_str": '{"category_summary": {...}, "advice": {...}}'}
     - Error Handling: If parsing fails, return basic summary without advice

Output Format:
  The final .invoke() return value must be a JSON string exactly like:
  {
    "category_summary": {
      "Groceries": 225.0,
      "Rent": 1005.0,
      "Utilities": 150.0,
      "Entertainment": 150.0
    },
    "advice": {
      "Groceries": "Consider meal planning and buying generic brands.",
      "Rent": "Look for ways to offset the slight overage.",
      "Entertainment": "Set a stricter entertainment budget."
    }
  }

Implementation Notes:
  - Use init_chat_model("anthropic:claude-3-5-sonnet-latest") for LLM calls
  - All state values are JSON strings, parse with json.loads()
  - Graph flow: START → categorizer → summarizer → advisor → END
  - Export as compiled_graph = graph_builder.compile()
      `.trim(),
      test_input: "Generate personal finance report",
      ground_truth:
        '{"category_summary": {"Groceries": 225.0, "Rent": 1005.0, "Utilities": 150.0, "Entertainment": 150.0}, "advice": {"Groceries": "Try shopping with a list and comparing prices to stay within your grocery budget.", "Rent": "Consider negotiating with your landlord or finding additional income to cover the rent overage.", "Entertainment": "Track entertainment spending more carefully and prioritize free activities."}}',
    },
  },
  {
    inputs: {
      repo: "mai-sandbox/open-swe_travel_agent",
      branch: "main",
      user_input: `
Build a comprehensive Travel Itinerary Planning Agent using LangGraph from scratch that can create detailed, cost-optimized travel plans with real-time pricing and logistics.

Requirements:

Multi-Tool Integration:
  - flight_searcher: Finds and compares flight options with real pricing
  - hotel_finder: Searches accommodations by budget, location, and dates
  - route_optimizer: Plans efficient daily itineraries and transportation
  - weather_checker: Provides weather forecasts for travel dates
  - budget_calculator: Tracks expenses and optimizes spending
  - currency_converter: Handles real-time currency exchange rates
  - visa_checker: Verifies visa requirements and travel documents

Conditional Routing Logic:
  The agent should intelligently route between:
    - Direct recommendations for simple queries (common destinations, general advice)
    - Single tool usage for specific requests (just flights, just hotels)
    - Multi-tool workflows for complete itinerary planning
    - Budget optimization workflows that balance cost vs. preferences

Stateful Memory:
  Implement persistent memory to:
    - Store user preferences (budget, travel style, dietary restrictions)
    - Remember previous searches and pricing data
    - Track multi-step planning progress
    - Maintain currency and date context throughout conversation

Workflow Types:
  - Simple Queries: Basic travel advice without real-time data needs
  - Price Check Workflows: Current pricing for flights, hotels, activities
  - Full Planning Workflows: Complete itineraries with optimization
  - Budget Analysis: Cost breakdowns and savings recommendations

Agent Architecture:
  - Use LangGraph's StateGraph with travel-specific state schema
  - Implement conditional edges for intelligent tool selection
  - Add budget constraint checking and optimization loops
  - Include error handling for unavailable dates/destinations

Expected Implementation:
  - Custom state schema with travel dates, budget, preferences, and pricing data
  - Tool definitions with proper input/output schemas and real-time API integration
  - Conditional routing function that decides between tools based on query complexity
  - Main planning workflow with cycles for optimization and user feedback
  - Memory checkpointing for session persistence across multi-day planning
      `.trim(),
      test_input:
        "Plan a 5-day trip to Tokyo from San Francisco for October 15-20, 2025. Budget is $2500 total for 1 person. I want mid-range accommodations and to visit major attractions.",
      ground_truth:
        "Should use multiple tools in sequence: flight_searcher ($784 round-trip SFO-Tokyo), hotel_finder ($143/night x 5 nights = $715), route_optimizer (Tokyo Skytree $18, DisneySea $65, Senso-ji free), budget_calculator (flights $784 + hotels $715 + food $350 + transport $70 + activities $150 = $2069, under budget), weather_checker (October Tokyo: 65°F, mild). Should provide day-by-day itinerary with specific costs and recommendations to stay within $2500 budget.",
    },
  },
  {
    inputs: {
      repo: "mai-sandbox/open-swe_edit_task_1a",
      branch: "main",
      user_input: `
I have a LangGraph React agent that I want to enhance with web search capabilities. Please add Tavily search functionality to this agent so it can search the web for current information and provide up-to-date responses.

Requirements:
  - Add Tavily search tool integration
  - Configure it to return 3 results with advanced search depth
  - Use environment variables for API keys (assume they will be in .env)
  - Maintain the existing conversation memory functionality
      `.trim(),
      test_input:
        "Who won the latest NBA championship and what were the final standings?",
      ground_truth:
        "The Oklahoma City Thunder (seeded 1st in the Western Conference) defeated the Indiana Pacers (seeded 4th in the Eastern Conference) 4-3 in the 2025 NBA Finals",
    },
  },
  {
    inputs: {
      repo: "mai-sandbox/open-swe_edit_task_1b",
      branch: "main",
      user_input: `
I have a LangGraph agent that has tools available but the workflow isn't routing correctly. The agent needs conditional logic to decide when to use tools versus when to provide a final answer directly.

Currently, the agent goes straight from the agent node to END, which means it never uses its available tools even when they would be helpful.

Please add conditional routing logic so the agent can:
  - Use tools when the agent decides tool calls are needed
  - Provide direct answers when no tools are required
  - Properly cycle between agent decisions and tool usage

The agent should be able to handle queries that need weather information, math calculations, or knowledge searches, as well as simple conversational queries that don't need tools.
      `.trim(),
      test_input: "Compare the weather in London and Tokyo right now",
      ground_truth:
        "In London, the current temperature is 66°F (19°C), feeling like 77°F (25°C), with cloudy skies and a 15% chance of rain, while Tokyo is experiencing light rain showers and a temperature of 80°F (27°C). (07/15/25)",
    },
  },
];
