export type StateType = {
  messages: any[]; // Using any for now, can be refined later
  ui?: any[];
  targetRepository?: { owner: string; repo: string };
};
