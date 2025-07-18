import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AlertCircle } from "lucide-react";
import { CollapsibleAlert } from "../collapsible-alert";
import { ErrorState } from "../types";

// Mock framer-motion to avoid animation issues in tests
jest.mock("framer-motion", () => ({
  motion: {
    div: ({ children, ...props }: any) => <div {...props}>{children}</div>,
  },
  AnimatePresence: ({ children }: any) => children,
}));

describe("CollapsibleAlert", () => {
  const mockIcon = <AlertCircle className="size-4" data-testid="alert-icon" />;

  describe("when ErrorState has no details", () => {
    const errorStateWithoutDetails: ErrorState = {
      message: "A simple error occurred",
    };

    it("renders the error message", () => {
      render(
        <CollapsibleAlert
          variant="destructive"
          errorState={errorStateWithoutDetails}
          icon={mockIcon}
        />
      );

      expect(screen.getByText("A simple error occurred")).toBeInTheDocument();
    });

    it("does not show the details section or expand button", () => {
      render(
        <CollapsibleAlert
          variant="destructive"
          errorState={errorStateWithoutDetails}
          icon={mockIcon}
        />
      );

      expect(screen.queryByText("Show details")).not.toBeInTheDocument();
      expect(screen.queryByText("Hide details")).not.toBeInTheDocument();
    });

    it("renders the icon", () => {
      render(
        <CollapsibleAlert
          variant="destructive"
          errorState={errorStateWithoutDetails}
          icon={mockIcon}
        />
      );

      expect(screen.getByTestId("alert-icon")).toBeInTheDocument();
    });
  });

  describe("when ErrorState has details (overloaded_error case)", () => {
    const errorStateWithDetails: ErrorState = {
      message: "An Anthropic overloaded error occurred. This error occurs when Anthropic APIs experience high traffic across all users.",
      details: "Error: overloaded_error - The API is currently experiencing high traffic. Please try again later.",
    };

    it("renders the error message", () => {
      render(
        <CollapsibleAlert
          variant="destructive"
          errorState={errorStateWithDetails}
          icon={mockIcon}
        />
      );

      expect(screen.getByText("An Anthropic overloaded error occurred. This error occurs when Anthropic APIs experience high traffic across all users.")).toBeInTheDocument();
    });

    it("shows the expand button initially", () => {
      render(
        <CollapsibleAlert
          variant="destructive"
          errorState={errorStateWithDetails}
          icon={mockIcon}
        />
      );

      expect(screen.getByText("Show details")).toBeInTheDocument();
      expect(screen.queryByText("Hide details")).not.toBeInTheDocument();
    });

    it("does not show details initially", () => {
      render(
        <CollapsibleAlert
          variant="destructive"
          errorState={errorStateWithDetails}
          icon={mockIcon}
        />
      );

      expect(screen.queryByText("Error: overloaded_error - The API is currently experiencing high traffic. Please try again later.")).not.toBeInTheDocument();
    });

    it("expands to show details when expand button is clicked", async () => {
      render(
        <CollapsibleAlert
          variant="destructive"
          errorState={errorStateWithDetails}
          icon={mockIcon}
        />
      );

      const expandButton = screen.getByText("Show details");
      fireEvent.click(expandButton);

      await waitFor(() => {
        expect(screen.getByText("Hide details")).toBeInTheDocument();
        expect(screen.getByText("Error: overloaded_error - The API is currently experiencing high traffic. Please try again later.")).toBeInTheDocument();
      });
    });

    it("collapses details when hide button is clicked", async () => {
      render(
        <CollapsibleAlert
          variant="destructive"
          errorState={errorStateWithDetails}
          icon={mockIcon}
        />
      );

      // First expand
      const expandButton = screen.getByText("Show details");
      fireEvent.click(expandButton);

      await waitFor(() => {
        expect(screen.getByText("Hide details")).toBeInTheDocument();
      });

      // Then collapse
      const collapseButton = screen.getByText("Hide details");
      fireEvent.click(collapseButton);

      await waitFor(() => {
        expect(screen.getByText("Show details")).toBeInTheDocument();
        expect(screen.queryByText("Error: overloaded_error - The API is currently experiencing high traffic. Please try again later.")).not.toBeInTheDocument();
      });
    });

    it("renders the icon", () => {
      render(
        <CollapsibleAlert
          variant="destructive"
          errorState={errorStateWithDetails}
          icon={mockIcon}
        />
      );

      expect(screen.getByTestId("alert-icon")).toBeInTheDocument();
    });
  });
});

