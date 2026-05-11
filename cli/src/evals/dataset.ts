// Sample coding tasks for evaluation with reference outputs
export const examples = [
    {
      inputs: { task: "Create a simple hello world function in TypeScript" },
      outputs: {
        output: `Here's a simple hello world function in TypeScript:

\`\`\`typescript
function helloWorld(name?: string): string {
  if (name) {
    return \`Hello, \${name}!\`;
  }
  return "Hello, World!";
}

// Usage examples:
console.log(helloWorld());        // "Hello, World!"
console.log(helloWorld("Alice")); // "Hello, Alice!"
\`\`\`

This function:
- Takes an optional name parameter
- Returns a personalized greeting if a name is provided
- Returns "Hello, World!" by default
- Uses TypeScript's optional parameter syntax with \`?\`
- Has a clear return type annotation (\`: string\`)`,
      },
    },
    {
      inputs: { task: "Explain what a React hook is" },
      outputs: {
        output: `A React hook is a special function that lets you use React features in functional components. Hooks were introduced in React 16.8 and allow you to use state and lifecycle features without writing class components.

Key characteristics:
- Start with "use" (e.g., useState, useEffect, useContext)
- Can only be called at the top level of components or custom hooks
- Cannot be used inside loops, conditions, or nested functions

Common built-in hooks:
- **useState**: Manages component state
- **useEffect**: Handles side effects (API calls, subscriptions)
- **useContext**: Accesses React context
- **useRef**: Creates mutable references
- **useMemo**: Memoizes expensive calculations
- **useCallback**: Memoizes callback functions

Example:
\`\`\`typescript
function Counter() {
  const [count, setCount] = useState(0);

  useEffect(() => {
    document.title = \`Count: \${count}\`;
  }, [count]);

  return <button onClick={() => setCount(count + 1)}>{count}</button>;
}
\`\`\``,
      },
    },
    {
      inputs: { task: "Write a function to check if a number is prime" },
      outputs: {
        output: `Here's an efficient function to check if a number is prime:

\`\`\`typescript
function isPrime(num: number): boolean {
  // Handle edge cases
  if (num <= 1) return false;
  if (num <= 3) return true;

  // Check for divisibility by 2 or 3
  if (num % 2 === 0 || num % 3 === 0) return false;

  // Check divisors of form 6k±1 up to √num
  for (let i = 5; i * i <= num; i += 6) {
    if (num % i === 0 || num % (i + 2) === 0) {
      return false;
    }
  }

  return true;
}

// Usage examples:
console.log(isPrime(17));  // true
console.log(isPrime(18));  // false
console.log(isPrime(2));   // true
console.log(isPrime(1));   // false
\`\`\`

This implementation:
- Uses the 6k±1 optimization (all primes > 3 are of form 6k±1)
- Only checks divisors up to √num for efficiency
- Time complexity: O(√n)
- Handles edge cases correctly (≤1, 2, 3)`,
      },
    },
    {
      inputs: { task: "How do I read a file in Node.js?" },
      outputs: {
        output: `There are several ways to read a file in Node.js:

**1. Async with Promises (Recommended):**
\`\`\`typescript
import { readFile } from 'fs/promises';

try {
  const data = await readFile('file.txt', 'utf8');
  console.log(data);
} catch (error) {
  console.error('Error reading file:', error);
}
\`\`\`

**2. Async with Callbacks:**
\`\`\`typescript
import { readFile } from 'fs';

readFile('file.txt', 'utf8', (err, data) => {
  if (err) {
    console.error('Error:', err);
    return;
  }
  console.log(data);
});
\`\`\`

**3. Synchronous (blocks execution):**
\`\`\`typescript
import { readFileSync } from 'fs';

try {
  const data = readFileSync('file.txt', 'utf8');
  console.log(data);
} catch (error) {
  console.error('Error:', error);
}
\`\`\`

**4. Streams (for large files):**
\`\`\`typescript
import { createReadStream } from 'fs';

const stream = createReadStream('file.txt', 'utf8');
stream.on('data', chunk => console.log(chunk));
stream.on('error', err => console.error(err));
\`\`\`

Best practices:
- Use async methods (promises) for non-blocking I/O
- Always specify encoding ('utf8') for text files
- Handle errors properly
- Use streams for large files to avoid memory issues`,
      },
    },
  ];