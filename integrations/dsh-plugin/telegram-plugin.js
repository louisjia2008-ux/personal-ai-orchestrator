/** Optional Cordis entrypoint for Telegram control of a local DeepSeek Harness. */

import {
  DeepSeekTelegramController,
  TelegramBotApi,
  TelegramStateStore,
  resolveTelegramConfig,
} from "./telegram.js";

export const name = "pao-telegram-controller";
export const inject = ["agents"];

export function apply(ctx, config = {}) {
  const resolved = resolveTelegramConfig(config);
  if (!resolved) {
    ctx.logger?.info?.("pao-telegram-controller disabled (opt-in only)");
    return;
  }

  const controller = new DeepSeekTelegramController({
    ctx,
    api: new TelegramBotApi({ token: resolved.token }),
    config: resolved,
    stateStore: new TelegramStateStore(resolved.stateFile),
    logger: ctx.logger,
  });

  ctx.effect(
    () => {
      controller.start();
      ctx.logger?.info?.(
        `pao-telegram-controller enabled for one fixed cwd; token source=${resolved.tokenEnv}`,
      );
      return () => controller.stop();
    },
    "pao-telegram-controller.lifecycle",
  );
}
