interface GitHubIssueSVGProps {
  width?: string;
  height?: string;
  className?: string;
}

export const GitHubIssueSVG = ({
  width = "100%",
  height = "100%",
  className,
}: GitHubIssueSVGProps) => (
  <svg
    viewBox="0 0 16 16"
    width={width}
    height={height}
    fill="currentColor"
    className={className}
  >
    <path d="M8 9.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z"></path>
    <path d="M8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0ZM1.5 8a6.5 6.5 0 1 0 13 0 6.5 6.5 0 0 0-13 0Z"></path>
  </svg>
);
