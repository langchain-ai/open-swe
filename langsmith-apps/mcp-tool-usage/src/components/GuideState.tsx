import { Badge } from '@langchain/macaw-components/Badge';
import { Banner } from '@langchain/macaw-components/Banner';
import { EmptyState } from '@langchain/macaw-components/EmptyState';
import { Spinner } from '@langchain/macaw-components/Spinner';
import { Text } from '@langchain/macaw-components/Text';

export function GuideState({
  step,
  heading,
  subtext,
  spinner,
  tone,
}: {
  step?: number;
  heading: string;
  subtext?: string;
  spinner?: boolean;
  tone?: 'error';
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-space-3 py-space-12">
      {spinner ? (
        <div role="status" className="flex items-center gap-space-3">
          <Spinner size="md" />
          <Text variant="sm" color="tertiary">
            {heading}
          </Text>
        </div>
      ) : tone === 'error' ? (
        <div role="alert">
          <Banner intent="error" title={heading}>
            {subtext}
          </Banner>
        </div>
      ) : (
        <>
          {step != null && <Badge color="primary">{`Step ${step}`}</Badge>}
          <EmptyState title={heading} description={subtext} />
        </>
      )}
    </div>
  );
}
