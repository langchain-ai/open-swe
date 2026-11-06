import type { JsonObject } from "@openswe/plugin-sdk"

export class UntrustedDocsContent {
  constructor(private readonly data: JsonObject) {}

  toJSON(): JsonObject {
    const json = JSON.stringify(this.data).replaceAll("<", "\\u003c")
    return {
      untrusted_content: `<untrusted_docs_data>\n${json}\n</untrusted_docs_data>`,
    }
  }
}
