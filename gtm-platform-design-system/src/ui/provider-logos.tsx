/*
 * Provider brand marks (Simple Icons / brand-kit paths).
 *
 * Same law as `logo.tsx`: these are brand assets, not Heroicons glyphs.
 * ProviderMark always mounts them inside IconWell at native colour. Slack is
 * the four-colour mark (not the purple mono glyph) so it reads as Slack.
 *
 * GoogleMark is the identity-provider G for the hosted sign-in door. It is not
 * a channel and must not join ProviderLogoId.
 */

import { cn } from "./cn";

type ProviderLogoId =
  | "gmail"
  | "slack"
  | "linkedin"
  | "salesforce"
  | "calendar"
  | "google-docs"
  | "notion"
  | "linear"
  | "pylon"
  | "hex";

interface LogoPath {
  d: string;
  fill: string;
  /** Inner subpaths cut holes, so a tile keeps its glyph as a cut-out. */
  fillRule?: "evenodd";
  /** Drawn only at native brand colour; a mono mark leaves it out. */
  brandOnly?: boolean;
}

const PROVIDER_LOGO: Record<ProviderLogoId, readonly LogoPath[]> = {
  /* Square symbol from https://www.usepylon.com, normalized from its 27.687 viewbox. */
  pylon: [{ d: "M23.6317 4.04813C21.0176 1.43673 17.5407 0 13.8416 0C10.1425 0 6.66563 1.43673 4.05335 4.04631C1.43923 6.65588 0 10.1249 0 13.8176C0 17.5102 1.43923 20.9792 4.05335 23.5888C6.66746 26.1984 10.1444 27.637 13.8434 27.637C17.5424 27.637 21.0194 26.2002 23.6336 23.5906C26.2477 20.9811 27.6869 17.512 27.6869 13.8194C27.6869 10.1268 26.2477 6.65771 23.6336 4.04813H23.6317ZM24.7956 13.8194C24.7956 19.2951 20.7128 23.9406 15.2863 24.6572V2.97792C20.7128 3.69445 24.7956 8.34185 24.7956 13.8176V13.8194ZM5.34389 6.9271C7.12273 4.74268 9.61936 3.34443 12.3987 2.97792V6.9271H5.34389ZM12.3987 9.81156V12.39H2.9831C3.09876 11.5067 3.32455 10.6398 3.65131 9.81156H12.3987ZM12.3987 15.2726V17.851H3.66233C3.3319 17.0227 3.10426 16.1559 2.98677 15.2726H12.3987ZM12.3987 20.7355V24.6572C9.63404 24.2925 7.14477 22.9034 5.36775 20.7355H12.3987Z", fill: "currentColor" }],
  /* Simple Icons: https://simpleicons.org/?q=googledocs */
  "google-docs": [
    {
      d: "M14.727 6.727H14V0H4.91c-.905 0-1.637.732-1.637 1.636v20.728c0 .904.732 1.636 1.636 1.636h14.182c.904 0 1.636-.732 1.636-1.636V6.727h-6zm-.545 10.455H7.09v-1.364h7.09v1.364zm2.727-3.273H7.091v-1.364h9.818v1.364zm0-3.273H7.091V9.273h9.818v1.363zM14.727 6h6l-6-6v6z",
      fill: "#4285F4",
    },
  ],
  gmail: [
    {
      d: "M24 5.457v13.909c0 .904-.732 1.636-1.636 1.636h-3.819V11.73L12 16.64l-6.545-4.91v9.273H1.636A1.636 1.636 0 0 1 0 19.366V5.457c0-2.023 2.309-3.178 3.927-1.964L5.455 4.64 12 9.548l6.545-4.91 1.528-1.145C21.69 2.28 24 3.434 24 5.457z",
      fill: "#EA4335",
    },
  ],
  /* Simple Icons Google Calendar — blue calendar shell, not a Gmail stand-in. */
  calendar: [
    {
      d: "M18.316 5.684H24v12.632A5.684 5.684 0 0 1 18.316 24H5.684A5.684 5.684 0 0 1 0 18.316V5.684A5.684 5.684 0 0 1 5.684 0h12.632A5.684 5.684 0 0 1 24 5.684ZM5.684 1.895A3.79 3.79 0 0 0 1.895 5.684v12.632a3.79 3.79 0 0 0 3.789 3.789h12.632a3.79 3.79 0 0 0 3.789-3.789V5.684a3.79 3.79 0 0 0-3.789-3.789Zm0 5.684h12.632v1.895H5.684Zm0 3.789h12.632v1.895H5.684Zm0 3.789h7.579v1.895H5.684Z",
      fill: "#4285F4",
    },
  ],
  linkedin: [
    {
      d: "M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433c-1.144 0-2.063-.926-2.063-2.065 0-1.138.92-2.063 2.063-2.063 1.14 0 2.064.925 2.064 2.063 0 1.139-.925 2.065-2.064 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z",
      fill: "#0A66C2",
    },
  ],
  /* Simple Icons: https://simpleicons.org/?q=linear */
  linear: [
    {
      d: "M2.886 4.18A11.982 11.982 0 0 1 11.99 0C18.624 0 24 5.376 24 12.009c0 3.64-1.62 6.903-4.18 9.105L2.887 4.18ZM1.817 5.626l16.556 16.556c-.524.33-1.075.62-1.65.866L.951 7.277c.247-.575.537-1.126.866-1.65ZM.322 9.163l14.515 14.515c-.71.172-1.443.282-2.195.322L0 11.358a12 12 0 0 1 .322-2.195Zm-.17 4.862 9.823 9.824a12.02 12.02 0 0 1-9.824-9.824Z",
      fill: "#5E6AD2",
    },
  ],
  /* Hex's app icon from https://hex.tech/favicon.svg: a navy tile with the glyph cut out, so a mono mark keeps
     it, and the pink glyph laid into the cut-out at brand colour. */
  hex: [
    { d: "M1.714 0H22.286A1.714 1.714 0 0 1 24 1.714V22.286A1.714 1.714 0 0 1 22.286 24H1.714A1.714 1.714 0 0 1 0 22.286V1.714A1.714 1.714 0 0 1 1.714 0ZM5.275 7.371V10.13H4.484V7.371H1.714V9.608V9.616V12.248V15.283H4.484V11.327H5.275V15.283H8.044V7.371H5.275ZM8.835 15.283H15.165V12.119H12.395V14.097H11.604V11.327H15.165V7.371H8.835V15.283ZM11.604 10.141V8.558H12.395V10.141H11.604ZM19.517 10.141V7.371H22.286V9.349L21.099 10.734L22.286 12.119V15.283H19.517V11.317H18.725V15.283H15.956V12.119L17.143 10.734L15.956 9.349V7.371H18.725V10.141H19.517Z", fill: "#01011B", fillRule: "evenodd" },
    { d: "M5.275 7.371V10.13H4.484V7.371H1.714V9.608V9.616V12.248V15.283H4.484V11.327H5.275V15.283H8.044V7.371H5.275ZM8.835 15.283H15.165V12.119H12.395V14.097H11.604V11.327H15.165V7.371H8.835V15.283ZM11.604 10.141V8.558H12.395V10.141H11.604ZM19.517 10.141V7.371H22.286V9.349L21.099 10.734L22.286 12.119V15.283H19.517V11.317H18.725V15.283H15.956V12.119L17.143 10.734L15.956 9.349V7.371H18.725V10.141H19.517Z", fill: "#F5C0C0", brandOnly: true },
  ],
  /* Simple Icons: https://simpleicons.org/?q=notion. Mono mark follows the theme. */
  notion: [
    {
      d: "M4.459 4.208c.746.606 1.026.56 2.428.466l13.215-.793c.28 0 .047-.28-.046-.326L17.86 1.968c-.42-.326-.981-.7-2.055-.607L3.01 2.295c-.466.046-.56.28-.374.466zm.793 3.08v13.904c0 .747.373 1.027 1.214.98l14.523-.84c.841-.046.935-.56.935-1.167V6.354c0-.606-.233-.933-.748-.887l-15.177.887c-.56.047-.747.327-.747.933zm14.337.745c.093.42 0 .84-.42.888l-.7.14v10.264c-.608.327-1.168.514-1.635.514-.748 0-.935-.234-1.495-.933l-4.577-7.186v6.952L12.21 19s0 .84-1.168.84l-3.222.186c-.093-.186 0-.653.327-.746l.84-.233V9.854L7.822 9.76c-.094-.42.14-1.026.793-1.073l3.456-.233 4.764 7.279v-6.44l-1.215-.139c-.093-.514.28-.887.747-.933zM1.936 1.035l13.31-.98c1.634-.14 2.055-.047 3.082.7l4.249 2.986c.7.513.934.653.934 1.213v16.378c0 1.026-.373 1.634-1.68 1.726l-15.458.934c-.98.047-1.448-.093-1.962-.747l-3.129-4.06c-.56-.747-.793-1.306-.793-1.96V2.667c0-.839.374-1.54 1.447-1.632z",
      fill: "currentColor",
    },
  ],
  salesforce: [
    {
      d: "M10.006 5.415a4.195 4.195 0 013.045-1.306c1.56 0 2.954.9 3.69 2.205.63-.3 1.35-.45 2.1-.45 2.85 0 5.159 2.34 5.159 5.22s-2.31 5.22-5.176 5.22c-.345 0-.69-.044-1.02-.104a3.75 3.75 0 01-3.3 1.95c-.6 0-1.155-.15-1.65-.375A4.314 4.314 0 018.88 20.4a4.302 4.302 0 01-4.05-2.82c-.27.062-.54.076-.825.076-2.204 0-4.005-1.8-4.005-4.05 0-1.5.811-2.805 2.01-3.51-.255-.57-.39-1.2-.39-1.846 0-2.58 2.1-4.65 4.65-4.65 1.53 0 2.85.705 3.72 1.8",
      fill: "#00A1E0",
    },
  ],
  /* Four-colour Slack mark — the purple mono silhouette is not the product logo. */
  slack: [
    {
      d: "M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52z",
      fill: "#E01E5A",
    },
    {
      d: "M6.313 15.165a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313z",
      fill: "#E01E5A",
    },
    {
      d: "M8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834z",
      fill: "#36C5F0",
    },
    {
      d: "M8.834 6.313a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312z",
      fill: "#36C5F0",
    },
    {
      d: "M18.956 8.834a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.522 2.521h-2.522V8.834z",
      fill: "#2EB67D",
    },
    {
      d: "M17.688 8.834a2.528 2.528 0 0 1-2.523 2.521 2.527 2.527 0 0 1-2.52-2.521V2.522A2.527 2.527 0 0 1 15.165 0a2.528 2.528 0 0 1 2.523 2.522v6.312z",
      fill: "#2EB67D",
    },
    {
      d: "M15.165 18.956a2.528 2.528 0 0 1 2.523 2.522A2.528 2.528 0 0 1 15.165 24a2.527 2.527 0 0 1-2.52-2.522v-2.522h2.52z",
      fill: "#ECB22E",
    },
    {
      d: "M15.165 17.688a2.527 2.527 0 0 1-2.52-2.523 2.526 2.526 0 0 1 2.52-2.52h6.313A2.527 2.527 0 0 1 24 15.165a2.528 2.528 0 0 1-2.522 2.523h-6.313z",
      fill: "#ECB22E",
    },
  ],
};

/*
 * RECEIVING mail providers, which are a different set from the channels above: these are who
 * takes delivery of what we send, not anything we connect to. A mark is here only where the
 * brand publishes a square symbol. Proofpoint, Mimecast and Barracuda publish a wordmark and
 * nothing else, and a wordmark at 16px is a grey smudge, so those keep a glyph that says what
 * they are instead of a mark nobody can read.
 *
 * Sources: Microsoft's four squares in its own brand colours; Yahoo and Cisco from Simple Icons
 * (CC0) in each brand's published hex. Google reuses GOOGLE_MARK below.
 */
type MailProviderLogoId = "microsoft" | "yahoo" | "cisco";

/* One square per quadrant: red, green, blue, yellow, as Microsoft arranges them. */
const MICROSOFT_SQUARE = 11.408;
const MICROSOFT_SECOND = 12.594;
const MAIL_PROVIDER_LOGO: Record<MailProviderLogoId, readonly LogoPath[]> = {
  microsoft: [
    { d: `M0 0h${MICROSOFT_SQUARE}v${MICROSOFT_SQUARE}H0z`, fill: "#F25022" },
    { d: `M${MICROSOFT_SECOND} 0H24v${MICROSOFT_SQUARE}H${MICROSOFT_SECOND}z`, fill: "#7FBA00" },
    { d: `M0 ${MICROSOFT_SECOND}h${MICROSOFT_SQUARE}V24H0z`, fill: "#00A4EF" },
    { d: `M${MICROSOFT_SECOND} ${MICROSOFT_SECOND}H24V24H${MICROSOFT_SECOND}z`, fill: "#FFB900" },
  ],
  yahoo: [{ d: "M18.86 1.56L14.27 11.87H19.4L24 1.56H18.86M0 6.71L5.15 18.27L3.3 22.44H7.83L14.69 6.71H10.19L7.39 13.44L4.62 6.71H0M15.62 12.87C13.95 12.87 12.71 14.12 12.71 15.58C12.71 17 13.91 18.19 15.5 18.19C17.18 18.19 18.43 16.96 18.43 15.5C18.43 14.03 17.23 12.87 15.62 12.87Z", fill: "#6001D2" }],
  cisco: [{ d: "M16.331 18.171V17.06l-.022.01c-.25.121-.522.19-.801.203a1.186 1.186 0 01-.806-.237 1.038 1.038 0 01-.352-.498 1.21 1.21 0 01-.023-.667c.052-.225.178-.426.357-.569.16-.134.355-.218.562-.242a1.85 1.85 0 011.061.198l.024.013v-1.117l-.051-.014a2.862 2.862 0 00-1.011-.132 2.34 2.34 0 00-.903.206c-.287.132-.54.327-.739.571a2.221 2.221 0 00-.04 2.705c.295.378.709.645 1.175.756.491.12 1.006.102 1.487-.052l.082-.023M5.336 18.171V17.06l-.022.01c-.25.121-.522.19-.801.203a1.183 1.183 0 01-.806-.237 1.03 1.03 0 01-.351-.498 1.202 1.202 0 01-.024-.667c.052-.225.177-.426.357-.569.16-.134.355-.218.562-.242a1.85 1.85 0 011.061.198l.024.013v-1.117l-.051-.014a2.862 2.862 0 00-1.011-.132 2.344 2.344 0 00-.903.206 2.08 2.08 0 00-.74.571 2.224 2.224 0 00-.041 2.705 2.11 2.11 0 001.176.756c.491.12 1.005.102 1.487-.052l.083-.023M9.26 17.249l-.004.957.07.012c.22.041.441.069.664.085.195.019.391.022.587.012.187-.014.372-.049.551-.104.21-.06.405-.163.571-.305a1.16 1.16 0 00.333-.478 1.31 1.31 0 00-.007-.96 1.068 1.068 0 00-.298-.414 1.261 1.261 0 00-.438-.255l-.722-.268a.388.388 0 01-.197-.188.245.245 0 01.008-.219.382.382 0 01.154-.142.798.798 0 01.257-.074c.153-.022.308-.021.46.005.18.02.358.051.533.096l.038.008v-.883l-.069-.015a4.749 4.749 0 00-.543-.097 2.844 2.844 0 00-.714-.003c-.3.027-.585.143-.821.33-.16.126-.281.293-.351.484-.104.29-.105.608 0 .899.054.145.14.274.252.381.097.093.207.173.327.236.157.084.324.149.497.195.057.017.114.035.17.054l.085.031.024.01c.084.03.162.078.226.14.045.042.08.094.101.151a.325.325 0 01.001.161.339.339 0 01-.166.198.856.856 0 01-.275.086 2.032 2.032 0 01-.427.021 5.208 5.208 0 01-.557-.074 9.195 9.195 0 01-.287-.067l-.033-.006zm-2.475.995h1.05v-4.167h-1.05v4.167zm12.162-2.936a1.095 1.095 0 011.541.158 1.094 1.094 0 01-.157 1.541l-.017.014a1.096 1.096 0 01-1.367-1.713m-1.525.854a2.193 2.193 0 002.666 2.107 2.139 2.139 0 00.701-3.937 2.207 2.207 0 00-3.367 1.83M22.961 10.728a.52.52 0 001.039 0V9.573a.52.52 0 00-1.039 0v1.155M20.117 10.728a.522.522 0 001.041 0V8.139a.521.521 0 00-1.04 0v2.589M17.231 11.771a.521.521 0 001.039 0V6.17a.52.52 0 00-1.039 0v5.601M14.393 10.728a.521.521 0 001.04 0V8.139a.52.52 0 00-1.039 0v2.589M11.494 10.728a.522.522 0 001.039 0V9.573a.52.52 0 00-1.039 0v1.155M8.624 10.728a.52.52 0 001.039 0V8.139a.52.52 0 00-1.039 0v2.589M5.737 11.771a.52.52 0 001.039 0V6.17a.52.52 0 00-1.039 0v5.601M2.876 10.728a.522.522 0 001.04 0V8.139a.52.52 0 00-1.039 0v2.589M0 10.728a.521.521 0 001.039 0V9.573a.52.52 0 00-1.039 0v1.155", fill: "#1BA0D7" }],
};

function isMailProviderLogoId(provider: string): provider is MailProviderLogoId {
  return provider in MAIL_PROVIDER_LOGO;
}

function isProviderLogoId(provider: string): provider is ProviderLogoId {
  return provider in PROVIDER_LOGO;
}

const GOOGLE_MARK: readonly LogoPath[] = [
  {
    d: "M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z",
    fill: "#4285F4",
  },
  {
    d: "M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z",
    fill: "#34A853",
  },
  {
    d: "M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z",
    fill: "#FBBC05",
  },
  {
    d: "M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z",
    fill: "#EA4335",
  },
];

function GoogleMark({
  className,
  title,
}: {
  className?: string;
  title?: string;
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
      className={cn("size-4 shrink-0", className)}
      role={title === undefined ? undefined : "img"}
      aria-hidden={title === undefined ? true : undefined}
      aria-label={title}
    >
      {GOOGLE_MARK.map((path) => (
        <path key={path.fill} d={path.d} fill={path.fill} />
      ))}
    </svg>
  );
}

interface ProviderLogoProps {
  provider: ProviderLogoId;
  /**
   * Native brand fills when true (default). Set false for a quiet mono mark
   * that inherits `currentColor` — settings list rows, etc.
   */
  branded?: boolean;
  className?: string;
  title?: string;
}

function ProviderLogo({
  branded = true,
  className,
  provider,
  title,
}: ProviderLogoProps) {
  const paths = PROVIDER_LOGO[provider];
  return (
    <svg
      viewBox={provider === "pylon" ? "0 0 27.687 27.687" : "0 0 24 24"}
      xmlns="http://www.w3.org/2000/svg"
      className={cn("size-4 shrink-0", className)}
      role={title === undefined ? undefined : "img"}
      aria-hidden={title === undefined ? true : undefined}
      aria-label={title}
    >
      {paths
        .filter((path) => branded || path.brandOnly !== true)
        .map((path) => (
          <path
            key={`${path.fill}-${path.d.slice(0, 24)}`}
            d={path.d}
            fill={branded ? path.fill : "currentColor"}
            fillRule={path.fillRule}
          />
        ))}
    </svg>
  );
}

interface MailProviderLogoProps {
  provider: MailProviderLogoId;
  className?: string;
  title?: string;
}

/** A receiving provider's own mark, at native brand colour like every other mark in this file. */
function MailProviderLogo({ className, provider, title }: MailProviderLogoProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
      className={cn("size-4 shrink-0", className)}
      role={title === undefined ? undefined : "img"}
      aria-hidden={title === undefined ? true : undefined}
      aria-label={title}
    >
      {MAIL_PROVIDER_LOGO[provider].map((path) => (
        <path key={path.fill} d={path.d} fill={path.fill} />
      ))}
    </svg>
  );
}

export { GoogleMark, MailProviderLogo, ProviderLogo, isMailProviderLogoId, isProviderLogoId };
export type { MailProviderLogoId, MailProviderLogoProps, ProviderLogoId, ProviderLogoProps };
