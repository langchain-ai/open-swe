import type { FeatureNode } from "./types.js";

export function formatFeatureContext({
  features,
  dependencies,
}: {
  features?: FeatureNode[];
  dependencies?: FeatureNode[];
}): string {
  const sections: string[] = [];

  if (features && features.length > 0) {
    sections.push("Primary features for this request:");
    for (const feature of features) {
      sections.push(formatFeatureLine(feature));
    }
  }

  const dependencyList = (dependencies ?? []).filter((dependency) =>
    features ? !features.some((feature) => feature.id === dependency.id) : true,
  );

  if (dependencyList.length > 0) {
    if (sections.length > 0) {
      sections.push("");
    }
    sections.push("Upstream dependencies to review:");
    for (const dependency of dependencyList) {
      sections.push(formatFeatureLine(dependency));
    }
  }

  if (sections.length === 0) {
    return "";
  }

  return `<feature_scope>\\n${sections.join("\\n")}\\n</feature_scope>`;
}

function formatFeatureLine(feature: FeatureNode): string {
  const summary: string[] = [
    `- ${feature.name ?? feature.id} (${feature.id})`,
  ];
  if (feature.description) {
    summary.push(`— ${feature.description}`);
  }
  if (feature.status) {
    summary.push(`— Status: ${feature.status}`);
  }
  return summary.join(" ");
}
