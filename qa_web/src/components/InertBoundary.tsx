import { type ReactNode, useLayoutEffect, useRef } from "react";

interface InertBoundaryProps {
  active: boolean;
  className?: string;
  children: ReactNode;
}

export function InertBoundary({ active, className, children }: InertBoundaryProps) {
  const boundaryRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const boundary = boundaryRef.current;
    if (!boundary) return;
    if (active) boundary.setAttribute("inert", "");
    else boundary.removeAttribute("inert");
  }, [active]);

  return (
    <div ref={boundaryRef} className={className} aria-hidden={active || undefined}>
      {children}
    </div>
  );
}
