export const formatTaskTitle = (
  plan: string,
  maxLength: number = 60,
): string => {
  if (!plan) return "Untitled Task";
  let title = plan.trim();
  title = title.replace(/(^\w|\.\s+\w)/g, (match) => match.toUpperCase());
  if (title.length > maxLength) {
    const truncated = title.substring(0, maxLength);
    const lastSpace = truncated.lastIndexOf(" ");
    if (lastSpace > maxLength * 0.7) {
      title = truncated.substring(0, lastSpace);
    } else {
      title = truncated + "...";
    }
  }

  return title;
};
