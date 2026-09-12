import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  MessageNotSentError,
  SimpleImageAttachmentAdapter,
  useExternalStoreRuntime,
} from "@assistant-ui/react"
import type {
  AppendMessage,
  AssistantRuntime,
  ExternalThreadQueueAdapter,
} from "@assistant-ui/react"
import type { ChatComposerProps } from "@/features/agents/components/composer/ChatComposer"
import type { MessagesProps } from "@/features/agents/components/messages/types"
import type { ImageChunk } from "@/features/agents/lib/types"
import { convertMessage } from "@/features/agents/components/assistant-ui/convertMessage"

export function appendMessageInput(message: AppendMessage) {
  const content = message.content
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n")
  const imageParts = [
    ...message.content
      .filter((part) => part.type === "image")
      .map((part) => ({ part, name: undefined as string | undefined })),
    ...(message.attachments ?? []).flatMap((attachment) =>
      attachment.content
        .filter((part) => part.type === "image")
        .map((part) => ({ part, name: attachment.name }))
    ),
  ]
  if (imageParts.length > 5)
    throw new Error("Attach up to 5 images per message.")
  const images: ImageChunk[] = imageParts.map(({ part, name }) => {
    const match = /^data:(image\/(?:png|jpeg|gif|webp));base64,(.*)$/s.exec(
      part.image
    )
    if (!match?.[1] || !match[2])
      throw new Error("Use PNG, JPEG, GIF, or WebP images.")
    if ((match[2].length * 3) / 4 > 10 * 1024 * 1024)
      throw new Error("Each image must be smaller than 10 MB.")
    return {
      kind: "image",
      mimeType: match[1],
      base64: match[2],
      fileName: name,
    }
  })
  return { content, images }
}

export interface ConversationRuntimeOptions {
  messages: MessagesProps["messages"]
  isStreaming: boolean
  isLoading?: boolean
  extras?: Record<string, unknown>
}

export interface ConversationRuntimeExtras {
  error: string | null
  sending: boolean
  failedMessages: { id: string; message: AppendMessage }[]
  dispatch: (message: AppendMessage) => void
  configureComposer: (composer: ChatComposerProps | undefined) => void
}

export function useConversationRuntime({
  messages,
  isStreaming,
  isLoading,
  extras,
}: ConversationRuntimeOptions) {
  const composerRef = useRef<ChatComposerProps | undefined>(undefined)
  const [disabled, setDisabled] = useState(true)
  const configureComposer = useCallback(
    (composer: ChatComposerProps | undefined) => {
      composerRef.current = composer
      setDisabled(!composer || !!composer.disabled)
    },
    []
  )
  const visibleMessages = useMemo(
    () => messages.filter((message) => !message.hidden),
    [messages]
  )
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [failedMessages, setFailedMessages] = useState<
    ConversationRuntimeExtras["failedMessages"]
  >([])
  const inFlight = useRef(false)
  const mounted = useRef(true)
  const runtimeRef = useRef<AssistantRuntime | null>(null)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const send = useCallback(async (message: AppendMessage) => {
    const composer = composerRef.current
    if (!composer || composer.disabled || inFlight.current)
      throw new MessageNotSentError("Sending is unavailable.")
    inFlight.current = true
    setSending(true)
    setError(null)
    try {
      const input = appendMessageInput(message)
      const model = composer.models?.find(
        (option) => option.id === composer.selection?.modelId
      )
      if (input.images.length && model?.supports_images === false)
        throw new Error("Choose a model that supports images.")
      if (!composer.onSubmit) throw new Error("Sending is unavailable.")
      await composer.onSubmit(input.content, input.images)
      setFailedMessages((previous) =>
        previous.filter((failed) => failed.message !== message)
      )
    } catch (cause) {
      if (!mounted.current) return
      setError(
        cause instanceof Error
          ? cause.message
          : "The message could not be sent."
      )
      const draft = runtimeRef.current?.thread.composer
      if (draft && !draft.getState().isEmpty) {
        setFailedMessages((previous) =>
          previous.some((failed) => failed.message === message)
            ? previous
            : [...previous, { id: crypto.randomUUID(), message }]
        )
        return
      }
      draft?.setText(
        message.content
          .filter((part) => part.type === "text")
          .map((part) => part.text)
          .join("\n")
      )
      for (const attachment of message.attachments ?? []) {
        await draft?.addAttachment({
          type: attachment.type,
          name: attachment.name,
          contentType: attachment.contentType,
          content: attachment.content,
        })
      }
      setFailedMessages((previous) =>
        previous.filter((failed) => failed.message !== message)
      )
    } finally {
      inFlight.current = false
      if (mounted.current) setSending(false)
    }
  }, [])
  const dispatch = useCallback(
    (message: AppendMessage) => {
      if (
        !composerRef.current ||
        composerRef.current.disabled ||
        inFlight.current
      )
        throw new MessageNotSentError("Sending is unavailable.")
      void send(message)
    },
    [send]
  )
  // Queue ownership stays with the backend, which can inject follow-ups into the active run.
  const queue: ExternalThreadQueueAdapter = {
    items: [],
    steerItems: [],
    enqueue: dispatch,
    steer: dispatch,
    move: () => {
      throw new Error("Queued messages are managed by the server.")
    },
    edit: () => {
      throw new Error("Queued messages are managed by the server.")
    },
    remove: () => {
      throw new Error("Queued messages are managed by the server.")
    },
  }
  const attachments = useMemo(() => {
    const adapter = new SimpleImageAttachmentAdapter()
    adapter.accept = "image/png,image/jpeg,image/gif,image/webp"
    return adapter
  }, [])
  const runtime = useExternalStoreRuntime({
    messages: visibleMessages,
    convertMessage,
    isRunning: isStreaming,
    isLoading,
    isDisabled: disabled,
    isSendDisabled: sending,
    onNew: send,
    queue,
    adapters: { attachments },
    extras: {
      error,
      sending,
      failedMessages,
      dispatch,
      configureComposer,
      ...extras,
    },
    onCancel: async () => {
      try {
        await composerRef.current?.onStop?.()
      } catch (cause) {
        if (mounted.current)
          setError(
            cause instanceof Error
              ? cause.message
              : "The run could not be stopped."
          )
      }
    },
  })
  useEffect(() => {
    runtimeRef.current = runtime
  }, [runtime])
  return runtime
}
