// Reports.tsx - Reports List Component with API Integration
import { FC, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { MdOutlineDeleteOutline } from 'react-icons/md';
import { Button, Card, DataTable, Dialog, Icon, Track } from 'components';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  ColumnFiltersState,
} from '@tanstack/react-table';
import { useToast } from 'hooks/useToast';
import { Link } from 'react-router-dom';
import {
  getReports,
  deleteReport,
  Report,
  ReportListParams,
} from 'services/reports';
import 'pages/Agency/AgencyList.scss';

const Reports: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();

  const [deleteModal, setDeleteModal] = useState<Report | null>(null);

  // Add table state for server-side pagination and sorting
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  // Convert sorting state to API format
  const getSortingParam = (sorting: SortingState): string => {
    if (sorting.length === 0) return '';

    const sort = sorting[0];
    let field = sort.id;

    // Map column IDs to API field names
    const fieldMap: Record<string, string> = {
      agency: 'agency_name',
      url: 'url',
      errors: 'errors',
      startedAt: 'scraping_started_at',
      finishedAt: 'scraping_finished_at',
    };

    field = fieldMap[field] || field;
    return `${field} ${sort.desc ? 'desc' : 'asc'}`;
  };

  // API query parameters
  const queryParams: ReportListParams = useMemo(
    () => ({
      page: pagination.pageIndex + 1,
      pageSize: pagination.pageSize,
      sorting: getSortingParam(sorting),
    }),
    [pagination.pageIndex, pagination.pageSize, sorting]
  );

  // Fetch reports data
  const {
    data: reportsApiData,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['reports', queryParams],
    queryFn: () => getReports(queryParams),
    keepPreviousData: true,
  });

  // Delete report mutation
  const deleteReportMutation = useMutation({
    mutationFn: deleteReport,
    onSuccess: async () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('reports.deleteSuccess'),
      });
      setDeleteModal(null);

      // Refetch to get updated data
      await queryClient.invalidateQueries(['reports']);

      // Check if current page is now out of bounds
      const newTotal = (reportsData.total || 0) - 1;
      const maxPages = Math.ceil(newTotal / pagination.pageSize);

      // Reset to last valid page if current page is out of bounds
      if (pagination.pageIndex >= maxPages && maxPages > 0) {
        setPagination({
          ...pagination,
          pageIndex: maxPages - 1,
        });
      } else if (maxPages === 0) {
        // If no data left, reset to page 0
        setPagination({
          ...pagination,
          pageIndex: 0,
        });
      }
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('reports.deleteError'),
      });
    },
  });

  // Transform API data
  const reportsData = useMemo(() => {
    if (!reportsApiData) return { data: [], total: 0 };

    return {
      data: reportsApiData.data,
      total: reportsApiData.total,
    };
  }, [reportsApiData]);

  // Handle pagination change
  const handlePaginationChange = (newPagination: PaginationState) => {
    setPagination(newPagination);
  };

  // Handle sorting change
  const handleSortingChange = (newSorting: SortingState) => {
    setSorting(newSorting);
  };

  // Handle delete confirmation
  const handleDeleteConfirm = () => {
    if (deleteModal) {
      deleteReportMutation.mutate(deleteModal.baseId);
    }
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

  const columns: ColumnDef<Report>[] = [
    {
      accessorKey: 'agencyName',
      id: 'agency',
      header: t('global.agency'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <div className="agencies__agency-cell">
          <Link
            to={`/reports/${row.original.baseId}`}
            style={{ textDecoration: 'underline', color: '#005AA3' }}
          >
            {row.original.agencyName}
          </Link>
        </div>
      ),
    },
    {
      accessorKey: 'url',
      header: t('global.domain'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <div className="agencies__agency-cell">{row.original.url}</div>
      ),
    },
    {
      accessorKey: 'errors',
      header: t('reports.errors'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <span
          style={{
            color: (row.original.errors || 0) > 0 ? '#D73E3E' : '#308653',
          }}
        >
          {row.original.errors || 0}
        </span>
      ),
    },
    {
      accessorKey: 'scrapingStartedAt',
      id: 'startedAt',
      header: t('reports.startedAt'),
      enableColumnFilter: false,
      cell: ({ row }) => formatDate(row.original.scrapingStartedAt),
    },
    {
      accessorKey: 'scrapingFinishedAt',
      id: 'finishedAt',
      header: t('reports.finishedAt'),
      enableColumnFilter: false,
      cell: ({ row }) => formatDate(row.original.scrapingFinishedAt),
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <Track gap={32} justify="end">
          <Button
            appearance="text"
            onClick={() => setDeleteModal(row.original)}
            className="agencies__action-btn"
            disabled={deleteReportMutation.isLoading}
          >
            <Icon
              icon={<MdOutlineDeleteOutline fontSize={20} />}
              size="medium"
            />
            {t('global.delete')}
          </Button>
        </Track>
      ),
    },
  ];

  // Show loading state
  if (isLoading) {
    return <div>Loading...</div>;
  }

  // Show error state
  if (error) {
    return <div>Error loading reports: {error.message}</div>;
  }

  return (
    <div className="agencies">
      <Track
        style={{ marginBottom: 16, minWidth: 800 }}
        justify="between"
        align="center"
      >
        <h1 className="h1">{t('reports.title')}</h1>
      </Track>

      <Card>
        <DataTable
          data={reportsData?.data ?? []}
          columns={columns}
          pagination={pagination}
          setPagination={handlePaginationChange}
          sorting={sorting}
          setSorting={handleSortingChange}
          columnFilters={columnFilters}
          setFiltering={setColumnFilters}
          sortable
          filterable
          pagesCount={reportsApiData?.totalPages ?? 0}
          isClientSide={false}
        />

        <div className="agencies__footer">
          <span className="agencies__total">
            {reportsData?.total ?? 0} {t('knowledgeBase.results')}
          </span>
        </div>
      </Card>

      {/* Delete Confirmation Modal */}
      {deleteModal && (
        <Dialog
          title={t('reports.deleteTitle')}
          onClose={() => setDeleteModal(null)}
          footer={
            <Track gap={16} justify="end">
              <Button
                appearance="secondary"
                onClick={() => setDeleteModal(null)}
                disabled={deleteReportMutation.isLoading}
              >
                {t('global.cancel')}
              </Button>
              <Button
                appearance="error"
                onClick={handleDeleteConfirm}
                disabled={deleteReportMutation.isLoading}
              >
                {deleteReportMutation.isLoading
                  ? t('global.deleting')
                  : t('global.delete')}
              </Button>
            </Track>
          }
        >
          <p>
            {t('reports.deleteConfirmation', {
              agency: deleteModal.agencyName,
              domain: deleteModal.url,
              interpolation: { escapeValue: false }
            })}
          </p>
        </Dialog>
      )}
    </div>
  );
};

export default Reports;
