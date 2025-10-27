import { cn } from "@/lib/utils";
import Image from "next/image";

export function NVIDIALogo({
  className,
  width = 200,
  height = 40,
  style,
  showSubtitle = false,
  useImage = false,
}: {
  width?: number;
  height?: number;
  className?: string;
  style?: React.CSSProperties;
  showSubtitle?: boolean;
  useImage?: boolean;
}) {
  return (
    <div className={cn("flex items-center gap-3", className)} style={style}>
      {/* NVIDIA Logo Image */}
      {useImage && (
        <Image
          src="/nvidia-logo.png"
          alt="NVIDIA"
          width={height * 2.5}
          height={height}
          className="object-contain"
          priority
        />
      )}

      {/* NVIDIA Text with Shimmer Animation */}
      <div className="flex flex-col gap-0.5">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold tracking-wider nvidia-shimmer whitespace-nowrap">
            NVIDIA
          </h1>
          <span className="text-lg font-semibold tracking-wide nvidia-shimmer-secondary whitespace-nowrap">
            NVCRM Agent Swarm
          </span>
        </div>
        {showSubtitle && (
          <span className="text-[10px] text-muted-foreground tracking-wide italic opacity-70">
            Powered by NVIDIA NIMs
          </span>
        )}
      </div>
    </div>
  );
}

