/**
 * Type guard to check if a value is a valid AgentInboxInterrupt schema.
 * @param value The value to check.
 * @returns True if the value matches the schema, false otherwise.
 */
export function isAgentInboxInterruptSchema(value: unknown): boolean {
  const valueAsObject = Array.isArray(value) ? value[0] : value;
  return (
    valueAsObject &&
    typeof valueAsObject === "object" &&
    "action_request" in valueAsObject &&
    typeof valueAsObject.action_request === "object" &&
    "config" in valueAsObject &&
    typeof valueAsObject.config === "object" &&
    "allow_respond" in valueAsObject.config &&
    "allow_accept" in valueAsObject.config &&
    "allow_edit" in valueAsObject.config &&
    "allow_ignore" in valueAsObject.config
  );
}
