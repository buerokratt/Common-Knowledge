import { FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { Button, Card, DataTable, Track } from 'components';
import { ColumnDef } from '@tanstack/react-table';
import { apiDev } from 'services/api';
import 'pages/Agency/AgencyList.scss';

interface ReportDetailItem {
  id: string;
  url: string;
  errorType: string;
  errorMessage: string;
  scraped: string;
}

interface ReportDetail {
  agency: string;
  domain: string;
  errors: ReportDetailItem[];
  total: number;
}

const Report: FC = () => {
  const { t } = useTranslation();
  const { agency, domain } = useParams<{ agency: string; domain: string }>();
  const [logType, setLogType] = useState<'cleaning' | 'scraping'>('cleaning');

  // Mock data - replace with actual API call
  const { data: reportData } = useQuery<ReportDetail>({
    queryKey: ['report-detail', agency, domain],
    queryFn: async () => ({
      agency: agency || 'EMTA',
      domain: domain || 'www.emta.ee',
      errors: [
        {
          id: '1',
          url: 'www.emta.ee/eraisik',
          errorType: '403 Forbidden',
          errorMessage: '403 Forbidden',
          scraped: '31.04.2025 14:53',
        },
        {
          id: '2',
          url: 'www.emta.ee/ettevote',
          errorType: '404 Not found',
          errorMessage: '404 Not found',
          scraped: '31.04.2025 14:53',
        },
        {
          id: '3',
          url: 'www.emta.ee/kontakt',
          errorType: '500 Internal server error',
          errorMessage: '500 Internal server error',
          scraped: '31.04.2025 14:53',
        },
      ],
      total: 170,
    }),
  });

  const columns: ColumnDef<ReportDetailItem>[] = [
    {
      accessorKey: 'url',
      header: t('knowledgeBase.url'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <a
          href={`https://${row.original.url}`}
          target="_blank"
          rel="noopener noreferrer"
          style={{ textDecoration: 'underline', color: '#005AA3' }}
        >
          {row.original.url}
        </a>
      ),
    },
    {
      accessorKey: 'errorType',
      header: t('reports.errorType'),
      enableColumnFilter: false,
      cell: ({ row }) => <span>{row.original.errorType}</span>,
    },
    {
      accessorKey: 'errorMessage',
      header: t('reports.errorMessage'),
      enableColumnFilter: false,
      cell: ({ row }) => <span>{row.original.errorMessage}</span>,
    },
    {
      accessorKey: 'scraped',
      header: t('knowledgeBase.scraped'),
      enableColumnFilter: false,
    },
  ];

  if (!reportData) {
    return <div>Loading...</div>;
  }

  return (
    <div className="agencies">
      <Track
        style={{ marginBottom: 24, minWidth: 800 }}
        justify="between"
        align="center"
      >
        <h1 className="h1">
          {reportData.agency} / {reportData.domain}
        </h1>
        <Track gap={12}>
          <Button
            appearance="secondary"
            style={{
              color: '#005AA3',
              borderColor: '#005AA3 !important',
              boxShadow: 'inset 0 0 0 2px #005AA3',
            }}
            onClick={() => setLogType('cleaning')}
          >
            {t('reports.cleaningLog')}
          </Button>
          <Button
            appearance="secondary"
            style={{
              color: '#005AA3',
              borderColor: '#005AA3 !important',
              boxShadow: 'inset 0 0 0 2px #005AA3',
            }}
            onClick={() => setLogType('scraping')}
          >
            {t('reports.scrapingLog')}
          </Button>
        </Track>
      </Track>

      <Card>
        <DataTable
          data={reportData.errors}
          columns={columns}
          pagination={{
            pageIndex: 0,
            pageSize: 10,
          }}
          sortable
          filterable
          pagesCount={Math.ceil(reportData.total / 10)}
        />

        <div className="agencies__footer">
          <span className="agencies__total">
            {reportData.total} {t('reports.results')}
          </span>
        </div>
      </Card>
    </div>
  );
};

export default Report;
