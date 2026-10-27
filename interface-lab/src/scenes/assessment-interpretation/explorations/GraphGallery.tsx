import { OrbitGraph } from './OrbitGraph';
import { LanesGraph } from './LanesGraph';
import { PetalsGraph } from './PetalsGraph';
import { MatrixGraph } from './MatrixGraph';
import { TimelineGraph } from './TimelineGraph';
import type { AssessmentGraphProps, AssessmentGraphVariant } from './graph-contract';

const graphs = { orbit: OrbitGraph, lanes: LanesGraph, petals: PetalsGraph, matrix: MatrixGraph, timeline: TimelineGraph };

export function GraphGallery({ variant, ...props }: AssessmentGraphProps & { variant: AssessmentGraphVariant }) {
  const Graph = graphs[variant];
  return <Graph {...props}/>;
}
