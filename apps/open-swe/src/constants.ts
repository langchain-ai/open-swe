import { SNAPSHOT_NAME } from "@open-swe/shared/constants";
import { CreateSandboxParams } from "@daytonaio/sdk";

export const DEFAULT_SANDBOX_CREATE_PARAMS: CreateSandboxParams = {
  resources: {
    cpu: 2,
    memory: 4,
    disk: 5,
  },
  user: "daytona",
  image: SNAPSHOT_NAME,
}