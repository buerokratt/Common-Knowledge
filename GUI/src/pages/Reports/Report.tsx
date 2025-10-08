import { FC, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { Button, Card, DataTable, Track, Tooltip } from 'components';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  ColumnFiltersState,
} from '@tanstack/react-table';
import {
  getReport,
  getReportPages,
  getDownloadUrl,
  ReportPage,
  ReportPagesListParams,
} from 'services/reports';
import 'pages/Agency/AgencyList.scss';

const Report: FC = () => {
  const { t } = useTranslation();
  const { id: reportBaseId } = useParams<{ id: string }>();
  const [logType, setLogType] = useState<'cleaning' | 'scraping'>('cleaning');

  // Add table state for server-side pagination and sorting
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  // Convert sorting state to API format
  const getSortingParam = (sorting: SortingState): string => {
    if (sorting.length === 0) return 'scraped_at desc';

    const sort = sorting[0];
    let field = sort.id;

    // Map column IDs to API field names
    const fieldMap: Record<string, string> = {
      url: 'url',
      errorType: 'error_type',
      errorMessage: 'error_message',
      scraped: 'scraped_at',
    };

    field = fieldMap[field] || field;
    return `${field} ${sort.desc ? 'desc' : 'asc'}`;
  };

  // API query parameters for pages
  const pagesQueryParams: ReportPagesListParams = useMemo(
    () => ({
      source_run_report_base_id: reportBaseId!,
      page: pagination.pageIndex + 1,
      pageSize: pagination.pageSize,
      sorting: getSortingParam(sorting),
    }),
    [reportBaseId, pagination.pageIndex, pagination.pageSize, sorting]
  );

  // Fetch report data
  const {
    data: reportData,
    isLoading: isLoadingReport,
    error: reportError,
  } = useQuery({
    queryKey: ['report', reportBaseId],
    queryFn: () => getReport(reportBaseId!),
    enabled: !!reportBaseId,
  });

  // Fetch report pages data
  const {
    data: reportPagesData,
    isLoading: isLoadingPages,
    error: pagesError,
  } = useQuery({
    queryKey: ['report-pages', pagesQueryParams],
    queryFn: () => getReportPages(pagesQueryParams),
    enabled: !!reportBaseId,
    keepPreviousData: true,
  });

  // Handle log download
  const handleLogDownload = async (logType: 'cleaning' | 'scraping') => {
    if (!reportData) return;

    try {
      const logUrl =
        logType === 'cleaning'
          ? reportData.cleaningLogUrl
          : reportData.scrapingLogUrl;

      if (!logUrl) {
        console.warn(`No ${logType} log URL available`);
        return;
      }

      const downloadData = await getDownloadUrl(logUrl);

      // Open the download URL in a new tab
      window.open(downloadData.downloadUrl, '_blank');
    } catch (error) {
      console.error(`Error downloading ${logType} log:`, error);
    }
  };

  // Transform pages data
  const pagesData = useMemo(() => {
    if (!reportPagesData) return { data: [], total: 0 };

    return {
      data: reportPagesData.data,
      total: reportPagesData.total,
    };
  }, [reportPagesData]);

  // Handle pagination change
  const handlePaginationChange = (newPagination: PaginationState) => {
    setPagination(newPagination);
  };

  // Handle sorting change
  const handleSortingChange = (newSorting: SortingState) => {
    setSorting(newSorting);
  };

  // Format date helper
  const formatDate = (dateString: string) => {
    if (!dateString) return '';
    return new Date(dateString).toLocaleDateString('et-EE', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const columns: ColumnDef<ReportPage>[] = [
    {
      accessorKey: 'url',
      header: t('knowledgeBase.url'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <Tooltip content={row.original.url}>
          <a
            href={row.original.url}
            target="_blank"
            rel="noopener noreferrer"
            style={{ textDecoration: 'underline', color: '#005AA3' }}
          >
            {row.original.url}
          </a>
        </Tooltip>
      ),
    },
    {
      accessorKey: 'errorType',
      header: t('reports.errorType'),
      enableColumnFilter: false,
      cell: ({ row }) => <span>{row.original.errorType || '-'}</span>,
    },
    {
      accessorKey: 'errorMessage',
      header: t('reports.errorMessage'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <div
          style={{
            maxWidth: 300,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          <Tooltip content={row.original.errorMessage || '-'}>
            <span>{row.original.errorMessage || '-'}</span>
          </Tooltip>
        </div>
      ),
    },
    {
      accessorKey: 'scrapedAt',
      id: 'scraped',
      header: t('knowledgeBase.scraped'),
      enableColumnFilter: false,
      cell: ({ row }) => formatDate(row.original.scrapedAt),
    },
  ];

  // Show loading state
  if (isLoadingReport || isLoadingPages) {
    return <div>Loading...</div>;
  }

  // Show error state
  if (reportError || pagesError) {
    return (
      <div>
        Error loading report: {reportError?.message || pagesError?.message}
      </div>
    );
  }

  if (!reportData) {
    return <div>Report not found</div>;
  }

  return (
    <div className="agencies">
      <Track
        style={{ marginBottom: 24, minWidth: 800 }}
        justify="between"
        align="center"
      >
        <Tooltip
          content={
            <>
              {reportData.agencyName} / {reportData.url}
            </>
          }
        >
          <h1
            className="h1"
            style={{
              maxWidth: 800,
              textOverflow: 'ellipsis',
              overflow: 'hidden',
            }}
          >
            {reportData.agencyName} / {reportData.url}
          </h1>
        </Tooltip>
        <Track gap={12}>
          <Button
            appearance="secondary"
            style={{
              color: '#005AA3',
              borderColor: '#005AA3 !important',
              boxShadow: 'inset 0 0 0 2px #005AA3',
            }}
            onClick={() => handleLogDownload('cleaning')}
            disabled={!reportData.cleaningLogUrl}
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
            onClick={() => handleLogDownload('scraping')}
            disabled={!reportData.scrapingLogUrl}
          >
            {t('reports.scrapingLog')}
          </Button>
        </Track>
      </Track>

      <Card>
        <DataTable
          data={pagesData?.data ?? []}
          columns={columns}
          pagination={pagination}
          setPagination={handlePaginationChange}
          sorting={sorting}
          setSorting={handleSortingChange}
          columnFilters={columnFilters}
          setFiltering={setColumnFilters}
          sortable
          filterable
          pagesCount={reportPagesData?.totalPages ?? 0}
          isClientSide={false}
        />

        <div className="agencies__footer">
          <span className="agencies__total">
            {pagesData?.total ?? 0} {t('knowledgeBase.results')}
          </span>
        </div>
      </Card>
    </div>
  );
};

export default Report;
