import { StudioCanvas } from "./studio/StudioCanvas";
import { ControlPlane } from "./studio/ControlPlane";

export default function Home() {
  return <ControlPlane><StudioCanvas /></ControlPlane>;
}
