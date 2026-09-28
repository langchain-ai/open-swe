export default function appleAppSiteAssociation() {
  return Response.json({
    applinks: {
      apps: [],
      details: [
        {
          appID: "H253X88X9F.com.langchain.openswe",
          paths: ["NOT /agents/local/*", "/agents/*", "/review", "/review/*"],
        },
      ],
    },
  })
}
