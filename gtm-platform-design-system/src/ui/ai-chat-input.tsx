"use client";

/*
 * PromptInput — gallery / reference composer with spring expand-collapse.
 *
 * Choreography from 21st.dev ai-chat-input (easemize): collapsed 48px pill,
 * spring grow to content height, maxWidth 320→480, attachment tray slide-up,
 * bottom chrome fade/blur, send/mic/stop morph. Retokenized onto CORE 14.
 * Product chat uses ChatInput, which mirrors this physics with mentions.
 */

import * as React from "react";
import { HostImage as Image } from "../host";
import { useCallback, useEffect, useRef, useState } from "react";

import { cn } from "./cn";
import { Icon } from "./icon";
import { ArrowUp, Orb, Plus, Square, X } from "./glyphs";

const SPRING_EASE = "cubic-bezier(0.175, 0.885, 0.32, 1.275)";
const SPRING_TRANSITION = `max-width 0.4s ${SPRING_EASE}, height 0.4s ${SPRING_EASE}`;
const SMOOTH_HEIGHT_TRANSITION = `max-width 0.4s ${SPRING_EASE}, height 0.15s ease-out`;

const COLLAPSED_HEIGHT = 48;
const EXPANDED_MIN_HEIGHT = 116;
const TEXTAREA_MIN = 68;
const TEXTAREA_MAX = 160;
const TRAY_HEIGHT = 68;
const MAX_WIDTH_COLLAPSED = 320;
const MAX_WIDTH_EXPANDED = 480;

interface Attachment {
  id: string;
  file: File;
  url: string;
  name: string;
  width?: number;
  height?: number;
}

function MorphingText({ text }: { text: string }) {
  const [width, setWidth] = useState<number | "auto">("auto");
  const spanRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (spanRef.current) {
      setWidth(spanRef.current.offsetWidth);
    }
  }, [text]);

  return (
    <span
      className="relative inline-flex items-center justify-center overflow-hidden transition-[width] duration-expressive ease-spring motion-reduce:transition-none"
      style={{ width }}
    >
      <span ref={spanRef} className="invisible whitespace-nowrap px-1">
        {text}
      </span>
      <span
        key={text}
        className="absolute inset-0 flex items-center justify-center whitespace-nowrap animate-in fade-in zoom-in-95 duration-expressive"
      >
        {text}
      </span>
    </span>
  );
}

function ModelMark({ model, className }: { model: string; className?: string }) {
  const monogram = model.trim().charAt(0).toUpperCase() || "?";
  return (
    <span
      className={cn(
        "inline-flex size-3.5 shrink-0 items-center justify-center rounded-full bg-muted text-meta font-semibold text-ink-muted scale-75",
        className
      )}
      aria-hidden
    >
      {model.toLowerCase().includes("opus") ||
      model.toLowerCase().includes("composer") ? (
        <Icon icon={Orb} size="sm" className="size-3 text-ink-muted" />
      ) : (
        monogram
      )}
    </span>
  );
}

function MicIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 14 14"
      fill="none"
      aria-hidden="true"
    >
      <rect
        x="5"
        y="1"
        width="4"
        height="7"
        rx="2"
        stroke="currentColor"
        strokeWidth="1.5"
      />
      <path
        d="M2.75 6.5V7a4.25 4.25 0 0 0 8.5 0v-.5M7 11.25V13"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function DynamicBarsIcon({ level }: { level: string }) {
  const isMediumOrHigh = level === "Medium" || level === "Max Effort";
  const isHigh = level === "Max Effort";

  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 14 14"
      fill="none"
      aria-hidden="true"
    >
      <rect
        x="1.5"
        y="8"
        width="2.5"
        height="4.5"
        rx="1"
        fill="currentColor"
        className="transition-opacity duration-expressive"
        opacity={1}
      />
      <rect
        x="5.75"
        y="5"
        width="2.5"
        height="7.5"
        rx="1"
        fill="currentColor"
        className="transition-opacity duration-expressive"
        opacity={isMediumOrHigh ? 1 : 0.3}
      />
      <rect
        x="10"
        y="2"
        width="2.5"
        height="10.5"
        rx="1"
        fill="currentColor"
        className="transition-opacity duration-expressive"
        opacity={isHigh ? 1 : 0.3}
      />
    </svg>
  );
}

function AttachmentThumb({
  attachment,
  index,
  onRemove,
  onOpen,
  registerRef,
}: {
  attachment: Attachment;
  index: number;
  onRemove: (id: string) => void;
  onOpen: (attachment: Attachment, rect: DOMRect) => void;
  registerRef: (id: string, el: HTMLButtonElement | null) => void;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const btnRef = useRef<HTMLButtonElement>(null);

  return (
    <button
      ref={(el) => {
        btnRef.current = el;
        registerRef(attachment.id, el);
      }}
      type="button"
      onMouseDown={(e) => e.preventDefault()}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={(e) => {
        e.stopPropagation();
        if (btnRef.current) {
          onOpen(attachment, btnRef.current.getBoundingClientRect());
        }
      }}
      style={{
        animationDelay: `${index * 35}ms`,
        animationFillMode: "backwards",
      }}
      className={cn(
        "group relative size-row-record shrink-0 overflow-hidden rounded-compact border border-line bg-muted outline-none",
        "transition-[scale] duration-expressive ease-spring hover:scale-[1.04] active:scale-[0.96] motion-reduce:transition-none",
        "animate-in fade-in slide-in-from-top-3 zoom-in-95 duration-expressive"
      )}
      aria-label={`Open preview of ${attachment.name}`}
    >
      <Image
        src={attachment.url}
        alt={attachment.name}
        fill
        unoptimized
        className="object-cover"
        draggable={false}
      />
      <span
        className={cn(
          "absolute inset-0 flex items-start justify-end bg-ink/0 transition-colors duration-fast ease-out-quint",
          isHovered && "bg-ink/25"
        )}
      >
        <span
          role="button"
          tabIndex={-1}
          onMouseDown={(e) => {
            e.preventDefault();
            e.stopPropagation();
          }}
          onClick={(e) => {
            e.stopPropagation();
            onRemove(attachment.id);
          }}
          className={cn(
            "m-1 flex size-badge items-center justify-center rounded-full bg-canvas/90 text-ink-muted shadow-control transition-[scale,opacity] duration-expressive ease-spring hover:bg-canvas hover:text-ink hover:scale-110",
            isHovered
              ? "scale-100 opacity-100"
              : "pointer-events-none scale-50 opacity-0"
          )}
          aria-label={`Remove ${attachment.name}`}
        >
          <Icon icon={X} size="sm" />
        </span>
      </span>
    </button>
  );
}

function AttachmentGalleryModal({
  attachment,
  originRect,
  onClose,
}: {
  attachment: Attachment;
  originRect: DOMRect;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<"opening" | "open" | "closing">("opening");
  const [targetRect, setTargetRect] = useState<{
    top: number;
    left: number;
    width: number;
    height: number;
    radius: number;
  } | null>(null);

  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      const maxW = Math.min(window.innerWidth * 0.86, 560);
      const maxH = Math.min(window.innerHeight * 0.78, 720);
      const naturalW = attachment.width || 800;
      const naturalH = attachment.height || 600;
      const scale = Math.min(maxW / naturalW, maxH / naturalH, 1.6);
      const width = naturalW * scale;
      const height = naturalH * scale;
      setTargetRect({
        top: (window.innerHeight - height) / 2,
        left: (window.innerWidth - width) / 2,
        width,
        height,
        radius: 20,
      });
      setPhase("open");
    });
    return () => cancelAnimationFrame(raf);
  }, [attachment]);

  const handleClose = useCallback(() => setPhase("closing"), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") handleClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [handleClose]);

  const isOpen = phase === "open";
  const isClosing = phase === "closing";
  const geometry =
    isOpen && targetRect
      ? targetRect
      : {
          top: originRect.top,
          left: originRect.left,
          width: originRect.width,
          height: originRect.height,
          radius: 12,
        };

  const animEasing = isClosing ? "ease-out" : SPRING_EASE;
  const animDur = isClosing ? "0.3s" : "0.45s";
  const flipTransition = `top ${animDur} ${animEasing}, left ${animDur} ${animEasing}, width ${animDur} ${animEasing}, height ${animDur} ${animEasing}, border-radius ${animDur} ${animEasing}`;

  return (
    <div
      className="fixed inset-0 z-50"
      onClick={handleClose}
      role="dialog"
      aria-modal="true"
      aria-label="Attachment preview"
    >
      <div
        className="absolute inset-0 bg-canvas/70 backdrop-blur-md transition-opacity duration-expressive"
        style={{ opacity: isOpen ? 1 : 0 }}
      />
      <div
        style={{
          position: "fixed",
          top: geometry.top,
          left: geometry.left,
          width: geometry.width,
          height: geometry.height,
          borderRadius: geometry.radius,
          transition: flipTransition,
          overflow: "hidden",
        }}
        className="bg-muted shadow-overlay"
        onTransitionEnd={() => {
          if (phase === "closing") onClose();
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <Image
          src={attachment.url}
          alt={attachment.name}
          fill
          unoptimized
          className="object-cover"
          draggable={false}
        />
      </div>
      <button
        type="button"
        onClick={handleClose}
        style={{
          opacity: isOpen ? 1 : 0,
          transform: isOpen ? "scale(1)" : "scale(0.7)",
        }}
        className={cn(
          "fixed top-4 right-4 flex size-control items-center justify-center rounded-full bg-panel/90 text-ink-muted shadow-popup backdrop-blur-sm",
          "transition-[scale,opacity] duration-expressive ease-spring hover:bg-panel hover:text-ink motion-reduce:transition-none",
          !isOpen && "pointer-events-none"
        )}
      >
        <Icon icon={X} />
      </button>
    </div>
  );
}

export interface PromptInputProps {
  onSubmit?: (
    value: string,
    meta: { model: string; effort: string; attachments: File[] }
  ) => void;
  placeholder?: string;
  className?: string;
  models?: string[];
  efforts?: string[];
  defaultValue?: string;
  value?: string;
  onChange?: (value: string) => void;
  maxAttachments?: number;
  /** When false (default), mic is decorative-only and never calls getUserMedia. */
  enableVoice?: boolean;
}

export const PromptInput = React.forwardRef<HTMLDivElement, PromptInputProps>(
  (
    {
      onSubmit,
      placeholder = "Ask anything",
      className,
      models = [
        "GPT 5.5",
        "Opus 4.8",
        "Gemini 3.5 Flash",
        "Composer 2.5",
        "GLM 5.2",
      ],
      efforts = ["Low", "Medium", "Max Effort"],
      defaultValue = "",
      value: controlledValue,
      onChange,
      maxAttachments = 6,
      enableVoice = false,
    },
    ref
  ) => {
    const [expanded, setExpanded] = useState(false);
    const [isSmoothResize, setIsSmoothResize] = useState(false);
    const [localValue, setLocalValue] = useState(defaultValue);
    const [selectedModel, setSelectedModel] = useState(models[0] ?? "GPT 5.5");
    const [effortIndex, setEffortIndex] = useState(1);
    const [isModelSelectOpen, setIsModelSelectOpen] = useState(false);
    const [attachments, setAttachments] = useState<Attachment[]>([]);
    const [activeAttachment, setActiveAttachment] = useState<{
      attachment: Attachment;
      rect: DOMRect;
    } | null>(null);
    const [isRecording, setIsRecording] = useState(false);
    const [audioData, setAudioData] = useState<number[]>(() =>
      new Array(5).fill(0)
    );
    const valueRef = useRef(
      controlledValue !== undefined ? controlledValue : localValue
    );
    const streamRef = useRef<MediaStream | null>(null);
    const audioContextRef = useRef<AudioContext | null>(null);
    const rafRef = useRef<number | null>(null);
    const recognitionRef = useRef<{
      stop: () => void;
      start: () => void;
      onresult: ((event: SpeechRecognitionEventLike) => void) | null;
      onerror: (() => void) | null;
      onend: (() => void) | null;
      continuous: boolean;
      interimResults: boolean;
    } | null>(null);
    const demoIntervalRef = useRef<number | null>(null);
    const demoTextIntervalRef = useRef<number | null>(null);
    const [hoverStyle, setHoverStyle] = useState({
      opacity: 0,
      transform: "translateY(0px) scale(0.95)",
      transition: "none",
    });
    const [textareaHeight, setTextareaHeight] = useState(TEXTAREA_MIN);
    const [isScrolling, setIsScrolling] = useState(false);

    const isControlled = controlledValue !== undefined;
    const value = isControlled ? controlledValue : localValue;
    const hasValue = value.trim() !== "" || attachments.length > 0;
    const hasAttachments = attachments.length > 0;
    const contentKeepsOpen =
      value.trim() !== "" || hasAttachments || isRecording;
    const showExpanded = expanded || contentKeepsOpen;
    const containerHeight = Math.max(
      EXPANDED_MIN_HEIGHT,
      textareaHeight + 48
    );

    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const internalContainerRef = useRef<HTMLDivElement>(null);
    const topFadeRef = useRef<HTMLDivElement>(null);
    const bottomFadeRef = useRef<HTMLDivElement>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const thumbRefs = useRef<Map<string, HTMLButtonElement | null>>(new Map());
    const attachmentsRef = useRef(attachments);

    useEffect(() => {
      attachmentsRef.current = attachments;
    }, [attachments]);

    useEffect(() => {
      valueRef.current = value;
    }, [value]);

    const updateFades = useCallback(() => {
      const el = textareaRef.current;
      if (!el) return;
      const { scrollTop, scrollHeight, clientHeight } = el;
      if (topFadeRef.current) {
        topFadeRef.current.style.opacity = Math.min(scrollTop / 20, 1).toString();
      }
      if (bottomFadeRef.current) {
        const bottomScroll = scrollHeight - clientHeight - scrollTop;
        bottomFadeRef.current.style.opacity = Math.min(
          Math.max(bottomScroll - 16, 0) / 10,
          1
        ).toString();
      }
    }, []);

    const handleValueChange = useCallback(
      (val: string) => {
        setIsSmoothResize(true);
        if (!isControlled) setLocalValue(val);
        onChange?.(val);
      },
      [isControlled, onChange]
    );

    const expand = () => {
      setIsSmoothResize(false);
      setExpanded(true);
    };

    const stopRecording = useCallback(() => {
      if (recognitionRef.current) {
        recognitionRef.current.stop();
        recognitionRef.current = null;
      }
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }
      if (audioContextRef.current) {
        void audioContextRef.current.close();
        audioContextRef.current = null;
      }
      if (demoIntervalRef.current) {
        window.clearInterval(demoIntervalRef.current);
        demoIntervalRef.current = null;
      }
      if (demoTextIntervalRef.current) {
        window.clearInterval(demoTextIntervalRef.current);
        demoTextIntervalRef.current = null;
      }
      setIsRecording(false);
      setAudioData(new Array(5).fill(0));
    }, []);

    const startRecording = useCallback(async () => {
      if (!enableVoice) return;
      setIsSmoothResize(false);
      setExpanded(true);

      let stream: MediaStream | null = null;
      try {
        if (navigator.mediaDevices?.getUserMedia) {
          stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        }
      } catch {
        stream = null;
      }

      setIsRecording(true);

      function simulateText() {
        const fakeText =
          "Can you draft a short follow-up for the Northwind renewal call?";
        const words = fakeText.split(" ");
        let i = 0;
        let currentBase = valueRef.current;
        demoTextIntervalRef.current = window.setInterval(() => {
          if (i < words.length) {
            currentBase = (currentBase ? `${currentBase} ` : "") + words[i];
            handleValueChange(currentBase);
            i++;
          } else {
            stopRecording();
          }
        }, 300);
      }

      if (stream) {
        streamRef.current = stream;
        const AudioCtx =
          window.AudioContext ||
          (
            window as unknown as {
              webkitAudioContext?: typeof AudioContext;
            }
          ).webkitAudioContext;
        if (!AudioCtx) {
          simulateText();
          return;
        }
        const audioCtx = new AudioCtx();
        audioContextRef.current = audioCtx;
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 64;
        const source = audioCtx.createMediaStreamSource(stream);
        source.connect(analyser);
        const dataArray = new Uint8Array(analyser.frequencyBinCount);
        const updateVisualizer = () => {
          analyser.getByteFrequencyData(dataArray);
          const bands = new Array(5).fill(0) as number[];
          const step = Math.floor(dataArray.length / 5);
          for (let i = 0; i < 5; i++) {
            let sum = 0;
            for (let j = 0; j < step; j++) {
              sum += dataArray[i * step + j] ?? 0;
            }
            bands[i] = sum / step / 255;
          }
          setAudioData(bands);
          rafRef.current = requestAnimationFrame(updateVisualizer);
        };
        updateVisualizer();

        const SpeechRecognitionCtor =
          (
            window as unknown as {
              SpeechRecognition?: new () => SpeechRecognitionLike;
              webkitSpeechRecognition?: new () => SpeechRecognitionLike;
            }
          ).SpeechRecognition ||
          (
            window as unknown as {
              webkitSpeechRecognition?: new () => SpeechRecognitionLike;
            }
          ).webkitSpeechRecognition;

        if (SpeechRecognitionCtor) {
          const recognition = new SpeechRecognitionCtor();
          recognition.continuous = true;
          recognition.interimResults = true;
          let baseline = valueRef.current;
          recognition.onresult = (event: SpeechRecognitionEventLike) => {
            let interimTranscript = "";
            let finalTranscript = "";
            for (let i = event.resultIndex; i < event.results.length; ++i) {
              const result = event.results[i];
              if (!result?.[0]) continue;
              if (result.isFinal) {
                finalTranscript += result[0].transcript;
              } else {
                interimTranscript += result[0].transcript;
              }
            }
            if (finalTranscript) {
              baseline += (baseline ? " " : "") + finalTranscript;
            }
            handleValueChange(
              (
                baseline + (interimTranscript ? ` ${interimTranscript}` : "")
              ).trim()
            );
          };
          recognition.onerror = () => {
            stopRecording();
          };
          recognition.onend = () => {
            stopRecording();
          };
          recognitionRef.current = recognition;
          recognition.start();
        } else {
          simulateText();
        }
      } else {
        demoIntervalRef.current = window.setInterval(() => {
          setAudioData(
            Array.from({ length: 5 }, () => Math.random() * 0.8 + 0.1)
          );
        }, 100);
        simulateText();
      }
    }, [enableVoice, handleValueChange, stopRecording]);

    useEffect(() => {
      if (isRecording && textareaRef.current) {
        textareaRef.current.scrollTop = textareaRef.current.scrollHeight;
      }
    }, [value, isRecording]);

    useEffect(() => {
      return () => {
        stopRecording();
        attachmentsRef.current.forEach((a) => URL.revokeObjectURL(a.url));
      };
    }, [stopRecording]);

    useEffect(() => {
      if (showExpanded && !isRecording) {
        const timer = setTimeout(() => {
          if (textareaRef.current) {
            textareaRef.current.focus();
            const length = textareaRef.current.value.length;
            textareaRef.current.setSelectionRange(length, length);
          }
        }, 50);
        return () => clearTimeout(timer);
      }
      return undefined;
    }, [showExpanded, isRecording]);

    useEffect(() => {
      if (!textareaRef.current) return;
      const el = textareaRef.current;
      const currentHeight = el.style.height;
      el.style.transition = "none";
      el.style.height = "0px";
      const scrollHeight = el.scrollHeight;
      el.style.height = currentHeight;
      void el.offsetHeight;
      el.style.transition = "";
      const newHeight = Math.max(
        TEXTAREA_MIN,
        Math.min(scrollHeight, TEXTAREA_MAX)
      );
      el.style.height = `${newHeight}px`;
      const frame = requestAnimationFrame(() => {
        setTextareaHeight(newHeight);
        setIsScrolling(scrollHeight > TEXTAREA_MAX);
        updateFades();
      });
      return () => cancelAnimationFrame(frame);
    }, [value, showExpanded, updateFades]);

    useEffect(() => {
      if (!isModelSelectOpen) return;
      const handleOutsideClick = (e: MouseEvent) => {
        if (
          internalContainerRef.current &&
          !internalContainerRef.current.contains(e.target as Node)
        ) {
          setIsModelSelectOpen(false);
        }
      };
      document.addEventListener("mousedown", handleOutsideClick);
      return () => document.removeEventListener("mousedown", handleOutsideClick);
    }, [isModelSelectOpen]);

    const handleBlur = (e: React.FocusEvent<HTMLDivElement>) => {
      if (
        internalContainerRef.current &&
        internalContainerRef.current.contains(e.relatedTarget as Node)
      ) {
        return;
      }
      if (value.trim() === "" && !hasAttachments && !isRecording) {
        setIsSmoothResize(false);
        setExpanded(false);
        setIsModelSelectOpen(false);
      }
    };

    const handleSubmit = () => {
      if (value.trim() === "" && !hasAttachments) return;
      setIsSmoothResize(false);
      onSubmit?.(value, {
        model: selectedModel,
        effort: efforts[effortIndex] ?? "Medium",
        attachments: attachments.map((a) => a.file),
      });
      handleValueChange("");
      attachments.forEach((a) => URL.revokeObjectURL(a.url));
      setAttachments([]);
      setExpanded(false);
      setIsModelSelectOpen(false);
    };

    const cycleEffort = (e: React.MouseEvent) => {
      e.stopPropagation();
      setEffortIndex((prev) => (prev + 1) % efforts.length);
    };

    const openFileChooser = (e: React.MouseEvent) => {
      e.stopPropagation();
      fileInputRef.current?.click();
    };

    const addAttachment = (
      file: File,
      url: string,
      width: number,
      height: number
    ) => {
      const id = `${file.name}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`;
      setAttachments((prev) => [
        ...prev,
        { id, file, url, name: file.name, width, height },
      ]);
    };

    const handleFilesChosen = (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []).filter((f) =>
        f.type.startsWith("image/")
      );
      e.target.value = "";
      if (files.length === 0) return;
      const room = Math.max(0, maxAttachments - attachments.length);
      const accepted = files.slice(0, room);
      if (!showExpanded) {
        setIsSmoothResize(false);
        setExpanded(true);
      } else {
        setIsSmoothResize(true);
      }
      for (const file of accepted) {
        const url = URL.createObjectURL(file);
        const img = new window.Image();
        img.onload = () =>
          addAttachment(file, url, img.naturalWidth, img.naturalHeight);
        img.onerror = () => addAttachment(file, url, 800, 600);
        img.src = url;
      }
    };

    const removeAttachment = (id: string) => {
      setIsSmoothResize(true);
      setAttachments((prev) => {
        const target = prev.find((a) => a.id === id);
        if (target) URL.revokeObjectURL(target.url);
        return prev.filter((a) => a.id !== id);
      });
      thumbRefs.current.delete(id);
    };

    const showArrow = hasValue && !isRecording;
    const showStop = isRecording;
    const showMic = enableVoice && !hasValue && !isRecording;
    const onActionButtonClick = (e: React.MouseEvent) => {
      e.preventDefault();
      if (isRecording) {
        stopRecording();
      } else if (hasValue) {
        handleSubmit();
      } else if (enableVoice) {
        void startRecording();
      } else {
        expand();
      }
    };

    const setContainerRef = useCallback(
      (node: HTMLDivElement | null) => {
        internalContainerRef.current = node;
        if (typeof ref === "function") {
          ref(node);
        } else if (ref) {
          ref.current = node;
        }
      },
      [ref]
    );

    const springOrSmooth = isSmoothResize
      ? SMOOTH_HEIGHT_TRANSITION
      : SPRING_TRANSITION;
    const trayTransition = isSmoothResize
      ? "height 0.15s ease-out"
      : `height 0.4s ${SPRING_EASE}`;
    const trayPanelTransition = isSmoothResize
      ? "transform 0.15s ease-out, opacity 0.15s ease-out"
      : `transform 0.4s ${SPRING_EASE}, opacity 0.3s ease-out`;

    return (
      <>
        <div
          ref={setContainerRef}
          onBlur={handleBlur}
          data-testid="prompt-input"
          data-expanded={showExpanded ? "true" : "false"}
          className={cn("relative flex w-full flex-col", className)}
          style={{
            maxWidth: showExpanded ? MAX_WIDTH_EXPANDED : MAX_WIDTH_COLLAPSED,
            transition: isSmoothResize
              ? "max-width 0.15s ease-out"
              : `max-width 0.4s ${SPRING_EASE}`,
          }}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            onChange={handleFilesChosen}
            className="hidden"
            tabIndex={-1}
            aria-hidden="true"
          />

          <div
            aria-hidden={!hasAttachments}
            data-testid="prompt-input-tray"
            style={{
              height: hasAttachments && showExpanded ? TRAY_HEIGHT : 0,
              transition: trayTransition,
            }}
            className="relative z-0 w-full overflow-hidden"
          >
            <div
              style={{
                position: "absolute",
                bottom: -8,
                left: 20,
                right: 20,
                height: TRAY_HEIGHT,
                transform:
                  hasAttachments && showExpanded
                    ? "translateY(0)"
                    : "translateY(100%)",
                opacity: hasAttachments && showExpanded ? 1 : 0,
                transition: trayPanelTransition,
              }}
              className="flex items-start gap-2 overflow-x-auto rounded-t-panel border border-b-0 border-line bg-muted px-2 pt-2 pb-1"
            >
              {attachments.map((attachment, index) => (
                <AttachmentThumb
                  key={attachment.id}
                  attachment={attachment}
                  index={index}
                  onRemove={removeAttachment}
                  onOpen={(a, rect) =>
                    setActiveAttachment({ attachment: a, rect })
                  }
                  registerRef={(id, el) => thumbRefs.current.set(id, el)}
                />
              ))}
            </div>
          </div>

          <div
            data-testid="prompt-input-card"
            onMouseDown={(e) => {
              const isTextarea = e.target === textareaRef.current;
              if (showExpanded && !isTextarea && !isRecording) {
                e.preventDefault();
                textareaRef.current?.focus();
              }
            }}
            style={{
              borderRadius: 24,
              height: showExpanded ? containerHeight : COLLAPSED_HEIGHT,
              transition: springOrSmooth,
              overflow: showExpanded ? "visible" : "hidden",
            }}
            className={cn(
              "relative z-10 w-full border border-line bg-panel shadow-control focus-within:border-primary/40 focus-within:ring-1 focus-within:ring-primary/20 hover:border-line-strong",
              showExpanded ? "cursor-text" : "cursor-default"
            )}
          >
            <textarea
              ref={textareaRef}
              value={value}
              onChange={(e) => handleValueChange(e.target.value)}
              onScroll={updateFades}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSubmit();
                }
                if (
                  e.key === "Escape" &&
                  value.trim() === "" &&
                  !hasAttachments
                ) {
                  setIsSmoothResize(false);
                  setExpanded(false);
                  setIsModelSelectOpen(false);
                }
              }}
              placeholder={placeholder}
              aria-label="Prompt"
              disabled={isRecording}
              style={{
                transition: isSmoothResize
                  ? "height 0.15s ease-out"
                  : `opacity 0.3s ease-out, transform 0.3s ease-out, height 0.4s ${SPRING_EASE}`,
              }}
              className={cn(
                "absolute inset-x-0 top-0 z-1 w-full resize-none bg-transparent py-3.5 pr-12 pl-4 text-body text-ink outline-none placeholder:font-medium placeholder:text-ink-subtle/80",
                showExpanded
                  ? "translate-y-0 scale-100 opacity-100"
                  : "pointer-events-none -translate-y-1 scale-95 opacity-0",
                isScrolling ? "overflow-y-auto" : "overflow-y-hidden",
                isRecording && "pointer-events-none"
              )}
            />

            <div
              ref={topFadeRef}
              className="pointer-events-none absolute top-0 left-4 z-2 h-control bg-gradient-to-b from-panel via-panel/90 to-transparent"
            />
            <div
              ref={bottomFadeRef}
              className="pointer-events-none absolute left-4 z-2 h-control bg-gradient-to-t from-panel via-panel/90 to-transparent"
              style={{
                opacity: 0,
                top: `${textareaHeight - 32}px`,
                transition: isSmoothResize
                  ? "top 0.15s ease-out"
                  : `top 0.4s ${SPRING_EASE}`,
              }}
            />

            <button
              type="button"
              onClick={expand}
              data-testid="prompt-input-collapsed-cta"
              style={{
                transition: isSmoothResize
                  ? "none"
                  : `opacity 0.4s ${SPRING_EASE}, transform 0.4s ${SPRING_EASE}`,
              }}
              className={cn(
                "absolute inset-x-0 top-0 z-1 cursor-text py-3.5 pr-12 pl-4 text-left text-body font-medium text-ink-subtle/80 outline-none",
                !showExpanded
                  ? "translate-y-0 scale-100 opacity-100"
                  : "pointer-events-none translate-y-1 scale-105 opacity-0"
              )}
              aria-label="Open prompt input"
            >
              {placeholder}
            </button>

            <div
              data-testid="prompt-input-bottom-actions"
              className={cn(
                "absolute bottom-2 left-3 z-10 flex items-center gap-0 transition-[opacity,transform,filter] duration-expressive ease-spring motion-reduce:transition-none",
                showExpanded && !isRecording
                  ? "pointer-events-auto translate-y-0 opacity-100 blur-none"
                  : "pointer-events-none translate-y-2 opacity-0 blur-sm"
              )}
            >
              <div className="relative">
                <button
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={(e) => {
                    e.stopPropagation();
                    setIsModelSelectOpen((prev) => !prev);
                  }}
                  className={cn(
                    "group flex items-center gap-1 rounded-full px-2 py-1 text-ink-muted transition-[background-color,color] duration-fast ease-out-quint outline-none hover:bg-hover hover:text-ink",
                    isModelSelectOpen && "bg-hover text-ink"
                  )}
                  aria-label={`Select model. Current: ${selectedModel}`}
                >
                  <ModelMark
                    model={selectedModel}
                    className="opacity-70 transition-opacity group-hover:opacity-100"
                  />
                  <span className="text-meta font-semibold select-none">
                    <MorphingText text={selectedModel} />
                  </span>
                </button>

                <div
                  style={{ transformOrigin: "bottom left" }}
                  onMouseLeave={() => {
                    setHoverStyle((prev) => ({
                      ...prev,
                      opacity: 0,
                      transform: prev.transform.replace(
                        "scale(1)",
                        "scale(0.95)"
                      ),
                      transition:
                        "opacity 0.2s ease-in, transform 0.2s ease-out",
                    }));
                  }}
                  className={cn(
                    "absolute bottom-full left-0 z-50 mb-2.5 flex w-44 flex-col gap-0.5 rounded-panel border border-line bg-panel/95 p-1 shadow-popup backdrop-blur-md transition-[opacity,transform] duration-expressive",
                    isModelSelectOpen
                      ? "pointer-events-auto translate-y-0 scale-100 opacity-100"
                      : "pointer-events-none translate-y-3 scale-95 opacity-0"
                  )}
                >
                  <div className="relative flex flex-col gap-0.5">
                    <div
                      style={hoverStyle}
                      className="pointer-events-none absolute top-0 right-0 left-0 -z-10 h-control rounded-compact bg-hover"
                    />
                    {models.map((model, idx) => (
                      <button
                        key={model}
                        type="button"
                        onMouseDown={(e) => e.preventDefault()}
                        onMouseEnter={() => {
                          setHoverStyle((prev) => ({
                            opacity: 1,
                            transform: `translateY(${idx * 34}px) scale(1)`,
                            transition:
                              prev.opacity === 0
                                ? "opacity 0.15s ease-out"
                                : `transform 0.3s ${SPRING_EASE}, opacity 0.15s ease`,
                          }));
                        }}
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedModel(model);
                          setIsModelSelectOpen(false);
                        }}
                        className="group relative flex h-control w-full items-center justify-between rounded-compact px-2.5 py-1.5 text-left text-meta font-medium text-ink-muted outline-none active:scale-[0.98]"
                      >
                        <span className="flex items-center gap-2">
                          <ModelMark model={model} />
                          {model}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={cycleEffort}
                className="group flex items-center gap-1 rounded-full px-2 py-1 text-ink-muted transition-[background-color,color] duration-fast ease-out-quint outline-none hover:bg-hover hover:text-ink"
              >
                <DynamicBarsIcon level={efforts[effortIndex] ?? "Medium"} />
                <span className="text-meta font-semibold select-none">
                  <MorphingText text={efforts[effortIndex] ?? "Medium"} />
                </span>
              </button>

              <button
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={openFileChooser}
                disabled={attachments.length >= maxAttachments}
                className="ml-auto flex size-control-sm items-center justify-center rounded-full text-ink-muted transition-[background-color,color] duration-fast ease-out-quint outline-none hover:bg-hover hover:text-ink disabled:pointer-events-none disabled:opacity-40"
              >
                <Icon icon={Plus} size="sm" />
              </button>
            </div>

            <div
              className={cn(
                "absolute right-12 bottom-2 z-10 flex h-control items-center justify-end gap-0.5 transition-[width,opacity,transform] duration-expressive ease-spring motion-reduce:transition-none",
                isRecording
                  ? "w-16 translate-x-0 opacity-100"
                  : "pointer-events-none w-0 translate-x-4 opacity-0"
              )}
            >
              {audioData.map((val, i) => (
                <div
                  key={i}
                  className="w-1 rounded-full bg-primary transition-[height] duration-fast ease-out-quint"
                  style={{ height: `${Math.max(4, val * 24)}px` }}
                />
              ))}
            </div>

            <button
              type="button"
              onMouseDown={(e) => {
                e.preventDefault();
                e.stopPropagation();
              }}
              onClick={onActionButtonClick}
              data-testid="prompt-input-action"
              aria-label={
                showArrow
                  ? "Send prompt"
                  : showStop
                    ? "Stop recording"
                    : enableVoice
                      ? "Use voice input"
                      : "Open prompt input"
              }
              className="absolute right-2 bottom-2 z-10 flex size-control items-center justify-center rounded-full bg-primary text-primary-ink transition-opacity duration-expressive outline-none hover:opacity-90 focus-visible:ring-2 focus-visible:ring-primary"
            >
              <span className="relative flex size-full items-center justify-center">
                <span
                  className={cn(
                    "absolute inset-0 flex items-center justify-center transition-[rotate,scale,opacity,filter] duration-expressive ease-spring motion-reduce:transition-none",
                    showArrow
                      ? "rotate-0 scale-100 opacity-100 blur-none"
                      : "pointer-events-none rotate-45 scale-50 opacity-0 blur-xs"
                  )}
                >
                  <Icon icon={ArrowUp} size="sm" />
                </span>
                <span
                  className={cn(
                    "absolute inset-0 flex items-center justify-center transition-[rotate,scale,opacity,filter] duration-expressive ease-spring motion-reduce:transition-none",
                    showMic
                      ? "rotate-0 scale-100 opacity-100 blur-none"
                      : "pointer-events-none -rotate-45 scale-50 opacity-0 blur-xs"
                  )}
                >
                  <MicIcon />
                </span>
                <span
                  className={cn(
                    "absolute inset-0 flex items-center justify-center transition-[rotate,scale,opacity,filter] duration-expressive ease-spring motion-reduce:transition-none",
                    showStop
                      ? "rotate-0 scale-100 opacity-100 blur-none"
                      : "pointer-events-none rotate-45 scale-50 opacity-0 blur-xs"
                  )}
                >
                  <Icon icon={Square} size="sm" />
                </span>
              </span>
            </button>
          </div>
        </div>

        {activeAttachment ? (
          <AttachmentGalleryModal
            attachment={activeAttachment.attachment}
            originRect={activeAttachment.rect}
            onClose={() => setActiveAttachment(null)}
          />
        ) : null}
      </>
    );
  }
);

PromptInput.displayName = "PromptInput";

interface SpeechRecognitionLike {
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
}

interface SpeechRecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<{
    isFinal: boolean;
    0?: { transcript: string };
  }>;
}
