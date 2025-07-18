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
      user_input: `Hey, we need a basic chat assistant for our project. Nothing too crazy, just something that can chat with users and handle a couple of simple tasks.

We want users to be able to:
- Have normal conversations with the assistant
- Ask it to search for stuff online when they need current info
- Get help with basic math calculations

The assistant should be smart enough to know when to use tools vs just chat normally.

- Use LangGraph for the main workflow (we're standardizing on this)
- Anthropic Claude for the LLM (we have API keys already)
- Keep it simple - this is a proof of concept

Just implement 2 tools:
1. **Search tool** - for when users ask about current events, facts, etc.
2. **Calculator tool** - for when they need math help

The agent should be able to converse with the user, search for info using the web tool, and use the calculator tool for arithmetic.

- Make sure it actually compiles and runs without errors
- Add type hints (our code standards require them)
- Handle errors gracefully - tools might fail sometimes
- Don't overthink the routing logic, simple is fine

The goal is to have a working assistant that demonstrates LangGraph basics. We'll probably extend it later with more features.`,
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
      user_input: `Hey, we need a really basic chatbot that can check the weather.

What we want:
- Users should be able to chat normally with the bot
- Ask about weather in any city
- Get helpful weather information when they need it

That's it! Keep it as simple as possible.

The tool:
Just one tool: Weather tool - looks up current weather for cities

The agent should be able to talk to user and answer any question related to weather using the weather tool.

Implementation notes:
- The bot should automatically detect weather questions
- Use the weather tool when needed, chat normally otherwise
- Keep the routing logic simple - if they mention a city + weather, use the tool
- Make sure it compiles and runs without errors`,
      test_input: "What's the weather in London right now?",
      ground_truth:
        "The weather in London is cloudy with a chance of rain, and about 58°F.",
    },
  },
  {
    inputs: {
      repo: "mai-sandbox/open-swe_ticket-Manager",
      branch: "main",
      user_input: `Hey, we need an advanced Support Ticket Triage agent built on LangGraph.

What we want:
• Support staff will feed in raw customer tickets (text).
• The agent should perform these steps in order:

1. **Classify** the ticket into one of three buckets: "Billing", "Technical", or "General Inquiry".
2. **Detect Priority** as "Low", "Medium", or "High" based on urgency clues.
3. **Summarize** the ticket in one clear sentence.
4. **Route** it to the correct email, using these rules:
   - Billing + High → priority-billing@company.com
   - Billing + else → billing@company.com
   - Technical + High → urgent-tech@company.com
   - Technical + else → tech@company.com
   - General Inquiry → support@company.com
5. **Draft an Acknowledgement** email snippet (1–2 sentences) referencing the summary and routing.

Key features:
1. **Classification Node** - LLM reads \`ticket_text\` → sets \`category\`.`,
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
      user_input: `Hey, we need a Multi-Agent News-Trend Alert System built on LangGraph.

What we want:
• Input: a topic string, e.g. "electric vehicles."
• The system invokes three agents in sequence—and **each node returns its result as a string**:

1. **Fetcher Agent** (\`fetcher\`)
   - Takes \`state["topic"]\` and uses a news-fetch tool to get the top 5 headlines.
   - Returns the **headlines list serialized as a JSON string**, and writes it into \`state["headlines_str"]\`.

2. **Analyzer Agent** (\`analyzer\`)
   - Reads \`state["headlines_str"]\` (a JSON string), parses it, then finds words appearing ≥2 times.
   - Returns the **trends list serialized as a JSON string**, and writes it into \`state["trends_str"]\`.

3. **Reporter Agent** (\`reporter\`)
   - Reads \`state["trends_str"]\` and \`state["topic"]\`.
   - If trends exist, creates this object:
     \`\`\`json
     {
       "trend_found": true,
       "trend_keywords": [...],
       "alert_summary": "In the latest headlines on {topic}, we saw repeated mentions of {keywords}."
     }
     \`\`\`
   - Otherwise:
     \`\`\`json
     {
       "trend_found": false,
       "alert_summary": "No new trend detected for {topic}."
     }
     \`\`\`
   - Returns that entire object **serialized as a single JSON string**.

Key features:
- **State TypedDict**:
  \`\`\`python
  class State(TypedDict):
      topic: str
      headlines_str: str  # JSON string of List[str]
      trends_str:    str  # JSON string of List[str]
  \`\`\``,
      test_input: "Oscars 2025 winners",
      ground_truth:
        '{ "trend_found": true, "trend_keywords": ["Oppenheimer", "Barbie"], "alert_summary": "In the latest headlines on Oscars 2025 winners, we saw repeated mentions of Oppenheimer and Barbie." }',
    },
  },
  {
    inputs: {
      repo: "mai-sandbox/open-swe_finance_tracker",
      branch: "main",
      user_input: `Hey, we need a Personal Finance Tracker agent built on LangGraph.

Requirements:
1. All node outputs and the final \`.invoke()\` return value must be a plain string.
2. The graph should start with a default initial state so it can run with an empty input:
   \`\`\`python
   transactions: "[]"
   category_budget: "{\\"Groceries\\":200, \\"Rent\\":1000, \\"Utilities\\":150, \\"Entertainment\\":100}"
   categorized_str: ""
   summary_str: ""
   \`\`\`

Workflow (three nodes, all returning JSON-encoded strings):

1. **Categorizer Node** (\`categorizer\`)
   * Input: \`state["transactions"]\` (JSON string of list of \`{date,description,amount}\`)
   * Action: Use an LLM to assign each transaction a \`"category"\` field.
   * Return: JSON string of the enriched list → stored into \`state["categorized_str"]\`.

2. **Summarizer Node** (\`summarizer\`)
   * Input: \`state["categorized_str"]\`
   * Action: Parse it and sum \`amount\` per category.
   * Return: JSON string of \`{category: total}\` → stored into \`state["summary_str"]\`.

3. **Advisor Node** (\`advisor\`)
   * Input: \`state["summary_str"]\` and \`state["category_budget"]\`
   * Action: Compare spending vs. budget. For any category over budget, use an LLM to write a tip.
   * Return: Final report JSON string:
     \`\`\`json
     {
       "category_summary": { ... },
       "advice": { "Groceries": "...", "Rent": "...", … }
     }
     \`\`\``,
      test_input: "Generate personal finance report",
      ground_truth:
        '{ "category_summary": {"Groceries": 225, "Rent": 1005, "Utilities": 150, "Entertainment": 150}, "advice": {} }',
    },
  },
  {
    inputs: {
      repo: "mai-sandbox/open-swe_finance_tracker",
      branch: "main",
      user_input: `Hey, we need a Personal Finance Tracker agent built on LangGraph.

Requirements:
1. All node outputs and the final \`.invoke()\` return value must be a plain string.
2. The graph should start with a default initial state so it can run with an empty input:
   \`\`\`python
   transactions: "[]"
   category_budget: "{\\"Groceries\\":200, \\"Rent\\":1000, \\"Utilities\\":150, \\"Entertainment\\":100}"
   categorized_str: ""
   summary_str: ""
   \`\`\`

Workflow (three nodes, all returning JSON-encoded strings):

1. **Categorizer Node** (\`categorizer\`)
   * Input: \`state["transactions"]\` (JSON string of list of \`{date,description,amount}\`)
   * Action: Use an LLM to assign each transaction a \`"category"\` field.
   * Return: JSON string of the enriched list → stored into \`state["categorized_str"]\`.

2. **Summarizer Node** (\`summarizer\`)
   * Input: \`state["categorized_str"]\`
   * Action: Parse it and sum \`amount\` per category.
   * Return: JSON string of \`{category: total}\` → stored into \`state["summary_str"]\`.

3. **Advisor Node** (\`advisor\`)
   * Input: \`state["summary_str"]\` and \`state["category_budget"]\`
   * Action: Compare spending vs. budget. For any category over budget, use an LLM to write a tip.
   * Return: Final report JSON string:
     \`\`\`json
     {
       "category_summary": { ... },
       "advice": { "Groceries": "...", "Rent": "...", … }
     }
     \`\`\``,
      test_input: "Show me my monthly spending overview",
      ground_truth:
        '{ "category_summary": {"Groceries": 225, "Rent": 1005, "Utilities": 150, "Entertainment": 150}, "advice": {} }',
    },
  },
  {
    inputs: {
      repo: "mai-sandbox/open-swe_travel_agent",
      branch: "main",
      user_input: `Build a comprehensive Travel Itinerary Planning Agent using LangGraph from scratch that can create detailed, cost-optimized travel plans with real-time pricing and logistics.

Requirements:

Multi-Tool Integration:
Create and integrate the following tools:
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
- Memory checkpointing for session persistence across multi-day planning`,
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
      user_input: `I have a LangGraph React agent that I want to enhance with web search capabilities. Please add Tavily search functionality to this agent so it can search the web for current information and provide up-to-date responses.

Requirements:
- Add Tavily search tool integration
- Configure it to return 3 results with advanced search depth
- Use environment variables for API keys (assume they will be in .env)
- Maintain the existing conversation memory functionality`,
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
      user_input: `I have a LangGraph agent that has tools available but the workflow isn't routing correctly. The agent needs conditional logic to decide when to use tools versus when to provide a final answer directly.

Currently, the agent goes straight from the agent node to END, which means it never uses its available tools even when they would be helpful.

Please add conditional routing logic so the agent can:
- Use tools when the agent decides tool calls are needed
- Provide direct answers when no tools are required
- Properly cycle between agent decisions and tool usage

The agent should be able to handle queries that need weather information, math calculations, or knowledge searches, as well as simple conversational queries that don't need tools.`,
      test_input: "Compare the weather in London and Tokyo right now",
      ground_truth:
        "In London, the current temperature is 66°F (19°C), feeling like 77°F (25°C), with cloudy skies and a 15% chance of rain, while Tokyo is experiencing light rain showers and a temperature of 80°F (27°C). (07/15/25)",
    },
  },
];
