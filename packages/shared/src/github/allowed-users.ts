export const ALLOWED_USERS = [
  "agola11",
  "akira",
  "aliyanishfaq",
  "andrewnguonly",
  "angus-langchain",
  "bracesproul",
  "ArthurLangChain",
  "baskaryan",
  "bvs-langchain",
  "catherine-langchain",
  "ccurme",
  "crystalro0",
  "dqbd",
  "emily-langchain",
  "eric-langchain",
  "EugeneJinXin",
  "eyurtsev",
  "gladwig2",
  "hari-dhanushkodi",
  "hinthornw",
  "hntrl",
  "hwchase17",
  "iakshay",
  "isahers1",
  "j-broekhuizen",
  "jacoblee93",
  "jdrogers940",
  "joaquin-borggio-lc",
  "katmayb",
  "keshivtandon",
  "langchain-infra",
  "lc-arjun",
  "lc-chad",
  "lnhsingh",
  "madams0013",
  "mdrxy",
  "mhk197",
  "nfcampos",
  "nhuang-lc",
  "nitboss",
  "PeriniM",
  "phvash",
  "QuentinBrosse",
  "rlancemartin",
  "romain-priour-lc",
  "samecrowder",
  "samnoyes",
  "starmorph",
  "suraj-langchain",
  "sydney-runkle",
  "tanushree-sharma",
  "victorm-lc",
  "xornivore",
  "xuro-langchain",
  "Palashio",
  "ads2280",
];

// HACK: Until we setup proper support for API credits, we will only allow users to self host Open SWE
export function isAllowedUser(username: string): boolean {
  const restrictToLangChainAuth =
    process.env.RESTRICT_TO_LANGCHAIN_AUTH === "true" ||
    process.env.NEXT_PUBLIC_RESTRICT_TO_LANGCHAIN_AUTH === "true";
  if (!restrictToLangChainAuth) {
    return true;
  }
  return ALLOWED_USERS.some((u) => u === username);
}
