import { FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { MdOutlineDeleteOutline } from 'react-icons/md';
import { Button, Card, DataTable, Dialog, Icon, Track } from 'components';
import { ColumnDef } from '@tanstack/react-table';
import { useToast } from 'hooks/useToast';
import { apiDev } from 'services/api';
import 'pages/Agency/AgencyList.scss';
import { Link } from 'react-router-dom';

interface ReportItem {
  id: string;
  agency: string;
  domain: string;
  errors: number;
  startedAt: string;
  finishedAt: string;
}

const Reports: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();

  const [deleteModal, setDeleteModal] = useState<ReportItem | null>(null);

  // Mock data - replace with actual API call
  const { data: reportsData, refetch } = useQuery<{
    data: ReportItem[];
    total: number;
  }>({
    queryKey: ['reports'],
    queryFn: async () => ({
      data: [
        {
          id: '1',
          agency: 'EMTA',
          domain: 'www.emta.ee',
          errors: 3,
          startedAt: '31.04.2025 14:53',
          finishedAt: '31.04.2025 15:53',
        },
        {
          id: '2',
          agency: 'EMTA',
          domain: 'www.emta.ee',
          errors: 0,
          startedAt: '31.05.2025 14:53',
          finishedAt: '31.05.2025 15:53',
        },
        {
          id: '3',
          agency: 'EMTA',
          domain: 'www.emta2.ee',
          errors: 2,
          startedAt: '30.04.2025 14:53',
          finishedAt: '31.04.2025 15:53',
        },
        {
          id: '4',
          agency: 'PPA',
          domain: 'www.politsei.ee',
          errors: 2,
          startedAt: '31.04.2025 14:53',
          finishedAt: '31.04.2025 15:53',
        },
        {
          id: '5',
          agency: 'Statistikaamet',
          domain: 'www.stat.ee',
          errors: 0,
          startedAt: '31.04.2025 14:53',
          finishedAt: '31.04.2025 15:53',
        },
      ],
      total: 170,
    }),
  });

  const handleDelete = async () => {
    if (!deleteModal) return;

    try {
      await apiDev.delete(`reports/${deleteModal.id}`);
      setDeleteModal(null);
      refetch();
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('reports.deleteSuccess'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('reports.deleteError'),
      });
    }
  };

  const columns: ColumnDef<ReportItem>[] = [
    {
      accessorKey: 'agency',
      header: t('global.agency'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <div className="agencies__agency-cell">
          <Link
            to={`/reports/${row.original.id}`}
            style={{ textDecoration: 'underline', color: '#005AA3' }}
          >
            {row.original.agency}
          </Link>
        </div>
      ),
    },
    {
      accessorKey: 'domain',
      header: t('global.domain'),
      enableColumnFilter: false,
    },
    {
      accessorKey: 'errors',
      header: t('reports.errors'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <span
          style={{ color: row.original.errors > 0 ? '#D73E3E' : '#308653' }}
        >
          {row.original.errors}
        </span>
      ),
    },
    {
      accessorKey: 'startedAt',
      header: t('reports.startedAt'),
      enableColumnFilter: false,
    },
    {
      accessorKey: 'finishedAt',
      header: t('reports.finishedAt'),
      enableColumnFilter: false,
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <Track gap={16} justify="end">
          <Button
            appearance="text"
            onClick={() => setDeleteModal(row.original)}
            className="agencies__action-btn"
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

  return (
    <div className="agencies">
      <Track style={{ marginBottom: 16 }} justify="between" align="center">
        <h1 className="h1">{t('reports.title')}</h1>
      </Track>

      <Card>
        <DataTable
          data={reportsData?.data ?? []}
          columns={columns}
          pagination={{
            pageIndex: 0,
            pageSize: 10,
          }}
          sortable
          filterable
          pagesCount={Math.ceil((reportsData?.total ?? 0) / 10)}
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
              >
                {t('global.cancel')}
              </Button>
              <Button appearance="error" onClick={handleDelete}>
                {t('global.delete')}
              </Button>
            </Track>
          }
        >
          <p>
            {t('reports.deleteConfirmation', {
              agency: deleteModal.agency,
              domain: deleteModal.domain,
            })}
          </p>
        </Dialog>
      )}
    </div>
  );
};

export default Reports;
