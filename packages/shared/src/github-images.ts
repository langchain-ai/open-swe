/**
 * Utility functions for extracting and validating image URLs from GitHub issue markdown content
 */

export interface ExtractedImage {
  url: string;
  altText?: string;
}

const TRUSTED_DOMAINS = [
  "github.com",
  "githubusercontent.com",
  "user-images.githubusercontent.com",
  "github-production-user-asset-6210df.s3.amazonaws.com",
  "private-user-images.githubusercontent.com",
  "camo.githubusercontent.com",
];

/**
 * Validates if a URL is publicly accessible and from a trusted domain
 * @param url - The URL to validate
 * @returns true if the URL is valid and publicly accessible
 */
export function isValidImageUrl(url: string): boolean {
  try {
    const parsedUrl = new URL(url);

    if (parsedUrl.protocol !== "https:") {
      return false;
    }

    const hostname = parsedUrl.hostname.toLowerCase();
    return TRUSTED_DOMAINS.some(
      (domain) => hostname === domain || hostname.endsWith(`.${domain}`),
    );
  } catch {
    return false;
  }
}

/**
 * Extracts image URLs from GitHub issue markdown content
 * Supports both ![alt](url) and <img src='url'> formats
 * @param content - The markdown content to extract images from
 * @returns Array of extracted and validated image objects
 */
export function extractImageUrls(content: string): ExtractedImage[] {
  const images: ExtractedImage[] = [];

  // Regex for markdown image syntax: ![alt text](url)
  const markdownImageRegex = /!\[([^\]]*)\]\(([^)]+)\)/g;

  // Regex for HTML img tags: <img src="url" alt="alt text"> or <img src='url' alt='alt text'>
  const htmlImageRegex =
    /<img[^>]+src=['"]([^'"]+)['"][^>]*(?:alt=['"]([^'"]*)['"])?[^>]*>/gi;

  let match;

  while ((match = markdownImageRegex.exec(content)) !== null) {
    const [, altText, url] = match;
    if (isValidImageUrl(url)) {
      images.push({
        url: url.trim(),
        altText: altText?.trim() || undefined,
      });
    }
  }

  while ((match = htmlImageRegex.exec(content)) !== null) {
    const [, url, altText] = match;
    if (isValidImageUrl(url)) {
      images.push({
        url: url.trim(),
        altText: altText?.trim() || undefined,
      });
    }
  }

  return images;
}
