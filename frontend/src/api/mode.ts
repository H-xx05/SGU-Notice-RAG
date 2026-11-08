/** Mock 开关的唯一读取点。默认 true —— 第一周没有后端也能跑全链路。 */
export function useMock(): boolean {
  const raw = import.meta.env.VITE_USE_MOCK
  // 只有显式写 "false" 才关；未配置时保持 true，避免新人拉下来一片红
  return (raw ?? 'true').toLowerCase() !== 'false'
}
