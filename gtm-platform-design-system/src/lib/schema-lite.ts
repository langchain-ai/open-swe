/*
 * The few generated API types the design system genuinely needs, declared here
 * so the package carries no dependency on a consumer's OpenAPI output.
 *
 * The shape mirrors openapi-typescript's `components["schemas"]` so extracted
 * modules keep their original import. A consumer with their own generated
 * schema can alias this module at their bundler and the types line up.
 */

export interface components {
  schemas: {
    /** Whether a collection reads the viewer's own rows or everything they may see. */
    DataScope: "MY" | "ALL";
  };
}
