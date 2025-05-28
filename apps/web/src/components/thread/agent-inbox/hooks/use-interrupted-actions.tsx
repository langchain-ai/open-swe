import { HumanResponseWithEdits, SubmitType } from "../types";
import {
  KeyboardEvent,
  Dispatch,
  SetStateAction,
  MutableRefObject,
  useState,
  useRef,
  useEffect,
} from "react";
import { createDefaultHumanResponse } from "../utils";
import { toast } from "sonner";
import { HumanInterrupt, HumanResponse } from "@langchain/langgraph/prebuilt";
import { END } from "@langchain/langgraph/web";
import { useStreamContext } from "@/providers/Stream";

interface UseInterruptedActionsInput {
  interrupt: HumanInterrupt;
}

interface UseInterruptedActionsValue {
  // Actions
  handleSubmit: (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent> | KeyboardEvent,
  ) => Promise<void>;
  handleIgnore: (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent>,
  ) => Promise<void>;
  handleResolve: (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent>,
  ) => Promise<void>;

  // State values
  streaming: boolean;
  streamFinished: boolean;
  loading: boolean;
  supportsMultipleMethods: boolean;
  hasEdited: boolean;
  hasAddedResponse: boolean;
  acceptAllowed: boolean;
  humanResponse: HumanResponseWithEdits[];

  // State setters
  setSelectedSubmitType: Dispatch<SetStateAction<SubmitType | undefined>>;
  setHumanResponse: Dispatch<SetStateAction<HumanResponseWithEdits[]>>;
  setHasAddedResponse: Dispatch<SetStateAction<boolean>>;
  setHasEdited: Dispatch<SetStateAction<boolean>>;

  // Refs
  initialHumanInterruptEditValue: MutableRefObject<Record<string, string>>;
}

export default function useInterruptedActions({
  interrupt,
}: UseInterruptedActionsInput): UseInterruptedActionsValue {
  const thread = useStreamContext();
  const [humanResponse, setHumanResponse] = useState<HumanResponseWithEdits[]>(
    [],
  );
  const [loading, setLoading] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [streamFinished, setStreamFinished] = useState(false);
  const initialHumanInterruptEditValue = useRef<Record<string, string>>({});
  const [selectedSubmitType, setSelectedSubmitType] = useState<SubmitType>();
  // Whether or not the user has edited any fields which allow editing.
  const [hasEdited, setHasEdited] = useState(false);
  // Whether or not the user has added a response.
  const [hasAddedResponse, setHasAddedResponse] = useState(false);
  const [acceptAllowed, setAcceptAllowed] = useState(false);

  useEffect(() => {
    try {
      const { responses, defaultSubmitType, hasAccept } =
        createDefaultHumanResponse(interrupt, initialHumanInterruptEditValue);
      setSelectedSubmitType(defaultSubmitType);
      setHumanResponse(responses);
      setAcceptAllowed(hasAccept);
    } catch (e) {
      console.error("Error formatting and setting human response state", e);
    }
  }, [interrupt]);

  const resumeRun = (response: HumanResponse[]): boolean => {
    try {
      thread.submit(
        {},
        {
          command: {
            resume: response,
          },
        },
      );
      return true;
    } catch (e: any) {
      console.error("Error sending human response", e);
      return false;
    }
  };

  const handleSubmit = async (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent> | KeyboardEvent,
  ) => {
    e.preventDefault();
    if (!humanResponse) {
      toast.error("Error", {
        description: "Please enter a response.",
        duration: 5000,
        richColors: true,
        closeButton: true,
      });
      return;
    }

    let errorOccurred = false;
    initialHumanInterruptEditValue.current = {};

    if (
      humanResponse.some((r) => ["response", "edit", "accept"].includes(r.type))
    ) {
      setStreamFinished(false);

      try {
        const humanResponseInput: HumanResponse[] = humanResponse.flatMap(
          (r) => {
            if (r.type === "edit") {
              if (r.acceptAllowed && !r.editsMade) {
                return {
                  type: "accept",
                  args: r.args,
                };
              } else {
                return {
                  type: "edit",
                  args: r.args,
                };
              }
            }

            if (r.type === "response" && !r.args) {
              // If response was allowed but no response was given, do not include in the response
              return [];
            }
            return {
              type: r.type,
              args: r.args,
            };
          },
        );

        const input = humanResponseInput.find(
          (r) => r.type === selectedSubmitType,
        );
        if (!input) {
          toast.error("Error", {
            description: "No response found.",
            richColors: true,
            closeButton: true,
            duration: 5000,
          });
          return;
        }

        setLoading(true);
        setStreaming(true);
        const resumedSuccessfully = resumeRun([input]);
        if (!resumedSuccessfully) {
          // This will only be undefined if the graph ID is not found
          // in this case, the method will trigger a toast for us.
          return;
        }

        toast("Success", {
          description: "Response submitted successfully.",
          duration: 5000,
        });

        if (!errorOccurred) {
          setStreamFinished(true);
        }
      } catch (e: any) {
        console.error("Error sending human response", e);

        if ("message" in e && e.message.includes("Invalid assistant ID")) {
          toast("Error: Invalid assistant ID", {
            description:
              "The provided assistant ID was not found in this graph. Please update the assistant ID in the settings and try again.",
            richColors: true,
            closeButton: true,
            duration: 5000,
          });
        } else {
          toast.error("Error", {
            description: "Failed to submit response.",
            richColors: true,
            closeButton: true,
            duration: 5000,
          });
        }

        errorOccurred = true;
        setStreaming(false);
        setStreamFinished(false);
      }

      if (!errorOccurred) {
        setStreaming(false);
        setStreamFinished(false);
      }
    } else {
      setLoading(true);
      resumeRun(humanResponse);

      toast("Success", {
        description: "Response submitted successfully.",
        duration: 5000,
      });
    }

    setLoading(false);
  };

  const handleIgnore = async (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent>,
  ) => {
    e.preventDefault();

    const ignoreResponse = humanResponse.find((r) => r.type === "ignore");
    if (!ignoreResponse) {
      toast.error("Error", {
        description: "The selected thread does not support ignoring.",
        duration: 5000,
      });
      return;
    }

    setLoading(true);
    initialHumanInterruptEditValue.current = {};

    resumeRun([ignoreResponse]);

    setLoading(false);
    toast("Successfully ignored thread", {
      duration: 5000,
    });
  };

  const handleResolve = async (
    e: React.MouseEvent<HTMLButtonElement, MouseEvent>,
  ) => {
    e.preventDefault();

    setLoading(true);
    initialHumanInterruptEditValue.current = {};

    try {
      thread.submit(
        {},
        {
          command: {
            goto: END,
          },
        },
      );

      toast("Success", {
        description: "Marked thread as resolved.",
        duration: 3000,
      });
    } catch (e) {
      console.error("Error marking thread as resolved", e);
      toast.error("Error", {
        description: "Failed to mark thread as resolved.",
        richColors: true,
        closeButton: true,
        duration: 3000,
      });
    }

    setLoading(false);
  };

  const supportsMultipleMethods =
    humanResponse.filter(
      (r) => r.type === "edit" || r.type === "accept" || r.type === "response",
    ).length > 1;

  return {
    handleSubmit,
    handleIgnore,
    handleResolve,
    humanResponse,
    streaming,
    streamFinished,
    loading,
    supportsMultipleMethods,
    hasEdited,
    hasAddedResponse,
    acceptAllowed,
    setSelectedSubmitType,
    setHumanResponse,
    setHasAddedResponse,
    setHasEdited,
    initialHumanInterruptEditValue,
  };
}
