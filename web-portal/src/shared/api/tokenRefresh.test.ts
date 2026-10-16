/** Refreshes are single-flight in a tab and serialized across tabs (D5). */
import { describe, expect, it, vi } from "vitest";

import { createTokenRefresher, type ExclusiveRunner } from "./tokenRefresh";

/** A runner that executes tasks one after the other, like a browser lock. */
function createQueueLock(): ExclusiveRunner {
  let tail: Promise<unknown> = Promise.resolve();
  return <Result>(task: () => Promise<Result>) => {
    const run = tail.then(task);
    tail = run.catch(() => undefined);
    return run;
  };
}

describe("createTokenRefresher", () => {
  it("sends one refresh for callers that ask at the same time", async () => {
    const requestNewAccessToken = vi.fn(() => Promise.resolve("token-1"));
    const refresh = createTokenRefresher(requestNewAccessToken, (task) =>
      task(),
    );

    const tokens = await Promise.all([refresh(), refresh(), refresh()]);

    expect(tokens).toEqual(["token-1", "token-1", "token-1"]);
    expect(requestNewAccessToken).toHaveBeenCalledTimes(1);
  });

  it("refreshes again once the previous refresh has finished", async () => {
    const requestNewAccessToken = vi
      .fn<() => Promise<string | null>>()
      .mockResolvedValueOnce("token-1")
      .mockResolvedValueOnce("token-2");
    const refresh = createTokenRefresher(requestNewAccessToken, (task) =>
      task(),
    );

    expect(await refresh()).toBe("token-1");
    expect(await refresh()).toBe("token-2");
  });

  it("lets a second tab refresh only after the first has finished", async () => {
    const lock = createQueueLock();
    const order: string[] = [];
    let releaseFirst: () => void = () => undefined;
    const firstTab = createTokenRefresher(
      () =>
        new Promise<string>((resolve) => {
          order.push("tab 1 starts");
          releaseFirst = () => {
            order.push("tab 1 ends");
            resolve("token-a");
          };
        }),
      lock,
    );
    const secondTab = createTokenRefresher(() => {
      order.push("tab 2 starts");
      return Promise.resolve("token-b");
    }, lock);

    const first = firstTab();
    const second = secondTab();
    await Promise.resolve();
    releaseFirst();

    expect(await Promise.all([first, second])).toEqual(["token-a", "token-b"]);
    expect(order).toEqual(["tab 1 starts", "tab 1 ends", "tab 2 starts"]);
  });

  it("reports an ended session as null and recovers on the next call", async () => {
    const requestNewAccessToken = vi
      .fn<() => Promise<string | null>>()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(null);
    const refresh = createTokenRefresher(requestNewAccessToken, (task) =>
      task(),
    );

    await expect(refresh()).rejects.toThrow("offline");
    expect(await refresh()).toBeNull();
  });
});
