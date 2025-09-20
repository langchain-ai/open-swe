/**
 * Random message selector for GitHub webhook responses
 */

export type WebhookMessageType =
  | "pr_review"
  | "pr_review_comment"
  | "pr_comment";

const PR_REVIEW_MESSAGES = [
  "🤖 I'll start working on [this PR review]({link}). Time to channel my inner code whisperer!",
  "🤖 Got your [PR review]({link})! Let me put on my debugging cape and get to work.",
  "🤖 [This review]({link}) looks interesting... Time to work some magic! ✨",
  "🤖 Challenge accepted! Working on [this PR review]({link}) now.",
  "🤖 I see you've summoned me for [this review]({link}). Let's make it happen! 🚀",
  "🤖 Time to dive into [this PR review]({link}). Hold my coffee, I'm going in!",
  "🤖 [This review]({link}) won't know what hit it. Starting work now!",
  "🤖 Beep boop! Processing [this PR review]({link}) with maximum efficiency.",
  "🤖 Your wish is my command! Tackling [this review]({link}) right away.",
  "🤖 Plot twist: I actually enjoy [reviews like this]({link}). Let's do this! 🎯",
];

const PR_REVIEW_COMMENT_MESSAGES = [
  "🤖 Interesting... I've received your [PR review comment]({link}). Time to work my magic!",
  "🤖 Spotted your [review comment]({link})! Let me channel my inner Sherlock Holmes. 🔍",
  "🤖 [This comment]({link}) has my full attention. Prepare for some serious code wizardry!",
  "🤖 Your [review comment]({link}) is now on my radar. Initiating fix sequence... 🎯",
  "🤖 I see what you did there with [this comment]({link}). Challenge accepted!",
  "🤖 [This review comment]({link}) looks spicy! 🌶️ Let me handle it with care.",
  "🤖 Roger that! Working on [your comment]({link}) with the precision of a Swiss watch.",
  "🤖 [This comment]({link}) activated my developer mode. Time to get things done!",
  "🤖 Your [review comment]({link}) is like a puzzle piece - let me find where it fits! 🧩",
  "🤖 Beep beep! [This comment]({link}) is now in my priority queue. Processing...",
];

const PR_COMMENT_MESSAGES = [
  "🤖 Got it! I'll start working on [this comment]({link}). Let the coding commence!",
  "🤖 [Your comment]({link}) has been received loud and clear! Time to make it happen.",
  "🤖 I see you've tagged me in [this comment]({link}). Consider it done! ✅",
  "🤖 [This comment]({link}) is now my main quest. Loading... please wait! 🎮",
  "🤖 Your [comment]({link}) just made my day! Let me work on this right away.",
  "🤖 Aha! [This comment]({link}) is exactly what I needed. Time to shine! ⭐",
  "🤖 [Your comment]({link}) activated my productivity mode. Buckle up!",
  "🤖 I'm on it! [This comment]({link}) is getting the VIP treatment. 👑",
  "🤖 [This comment]({link}) speaks to my soul. Let me craft the perfect solution!",
  "🤖 Bingo! [Your comment]({link}) is now in my capable digital hands. Watch this space! 🚀",
];

/**
 * Selects a random message for the specified webhook type
 */
export function getRandomWebhookMessage(
  type: WebhookMessageType,
  linkToTrigger: string,
): string {
  let messages: string[];

  switch (type) {
    case "pr_review":
      messages = PR_REVIEW_MESSAGES;
      break;
    case "pr_review_comment":
      messages = PR_REVIEW_COMMENT_MESSAGES;
      break;
    case "pr_comment":
      messages = PR_COMMENT_MESSAGES;
      break;
    default:
      throw new Error(`Unknown webhook message type: ${type}`);
  }

  const randomIndex = Math.floor(Math.random() * messages.length);
  const selectedMessage = messages[randomIndex];

  return selectedMessage.replace("{link}", linkToTrigger);
}
