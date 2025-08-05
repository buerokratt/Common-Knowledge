import { FC } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { getSource } from 'services/sources';
import ScrapedFiles from 'pages/ScrapedFiles';
import UploadedFiles from 'pages/UploadedFiles';

const Files: FC = () => {
  const { id: sourceId } = useParams<{ id: string }>();

  // Fetch source data to determine type
  const {
    data: sourceData,
    isLoading,
    error,
  } = useQuery({
    queryKey: ['source', sourceId],
    queryFn: () => getSource(sourceId!),
    enabled: !!sourceId,
  });

  // Show loading state
  if (isLoading) {
    return <div>Loading...</div>;
  }

  // Show error state
  if (error) {
    return <div>Error loading source data</div>;
  }

  // Show error if source not found
  if (!sourceData) {
    return <div>Source not found</div>;
  }

  // Route to appropriate component based on source type
  switch (sourceData.type) {
    case 'url_to_scrape':
      return <ScrapedFiles />;
    case 'file':
      return <UploadedFiles />;
    default:
      return <div>Unknown source type: {sourceData.type}</div>;
  }
};

export default Files;
