import { ScenePackageCanvas } from "./ScenePackageCanvas";

/** 参考图 Canvas：当前与场景包共用分镜面，后续可拆独立素材视图。 */
export function SceneAssetCanvas(props: Parameters<typeof ScenePackageCanvas>[0]) {
  return <ScenePackageCanvas {...props} />;
}
