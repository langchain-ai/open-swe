import { createContext, useContext } from "react";
import { TokenExpiredError } from "@lib/api-types";
import type { ApiClient } from "@lib/api-client";
import type { DeploymentConfig } from "@lib/api-types";

export type ExpiredAuthContextValue = {
  markExpired: (d: DeploymentConfig) => void;
};

export const ExpiredAuthContext = createContext<ExpiredAuthContextValue>({
  markExpired: () => {
    /* default no-op; provider injected at the root */
  },
});

export const useExpiredAuth = (): ExpiredAuthContextValue =>
  useContext(ExpiredAuthContext);

/**
 * Wrap an ApiClient so any method that throws TokenExpiredError fires
 * `onExpired(deployment)` before re-throwing. The proxy preserves method
 * binding so callers can keep destructuring methods if needed.
 */
export const wrapApi = (
  api: ApiClient,
  deployment: DeploymentConfig,
  onExpired: (d: DeploymentConfig) => void,
): ApiClient => {
  return new Proxy(api, {
    get(target, prop, receiver) {
      const orig = Reflect.get(target, prop, receiver) as unknown;
      if (typeof orig !== "function") return orig;
      return (...args: unknown[]) => {
        try {
          const result = (orig as (...a: unknown[]) => unknown).apply(
            target,
            args,
          );
          if (result instanceof Promise) {
            return result.catch((err: unknown) => {
              if (err instanceof TokenExpiredError) {
                onExpired(deployment);
              }
              throw err;
            });
          }
          return result;
        } catch (err) {
          if (err instanceof TokenExpiredError) {
            onExpired(deployment);
          }
          throw err;
        }
      };
    },
  });
};
