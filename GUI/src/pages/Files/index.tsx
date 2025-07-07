import { FC, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import {
  MdDeleteOutline,
  MdOutlineViewColumn,
  MdOutlineTableChart,
  MdGridView,
} from 'react-icons/md';
import {
  Button,
  Card,
  DataTable,
  Dialog,
  FormInput,
  Icon,
  Track,
  SwitchBox,
  FileUploader,
  Editor,
} from 'components';
import { ColumnDef } from '@tanstack/react-table';
import { useToast } from 'hooks/useToast';
import { apiDev } from 'services/api';
import 'pages/Agency/AgencyList.scss';
import type { FileItem } from 'components/FileUploader/FileUploader';

interface UrlItem {
  id: string;
  name: string;
  uploaded: string;
  domain: string;
}

interface FormData {
  url: string;
  search: string;
  files: FileItem[];
}

const KnowledgeBaseDetail: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const { agency, domain } = useParams<{ agency: string; domain: string }>();

  const [addUrlModal, setAddUrlModal] = useState(false);
  const [deleteModal, setDeleteModal] = useState<UrlItem | null>(null);
  const [editorState, setEditorState] = useState<
    'raw' | 'cleaned' | 'edited' | null
  >(null);
  const [formData, setFormData] = useState<FormData>({
    url: '',
    search: '',
    files: [],
  });

  // Mock data - replace with actual API call
  const { data: urlData, refetch } = useQuery<{
    data: UrlItem[];
    total: number;
  }>({
    queryKey: ['knowledge-base-urls', agency, domain],
    queryFn: async () => ({
      data: [
        {
          id: '1',
          name: 'megadata.html',
          uploaded: '06.05.2024 10:08',
          domain: 'Eraisik',
        },
        {
          id: '2',
          name: 'https://www.riigiteataja.ee/akt/324092024001',
          uploaded: '06.05.2024 10:08',
          domain: 'Ettevõte',
        },
        {
          id: '3',
          name: 'https://www.riigiteataja.ee/akt/324092024001',
          uploaded: '06.05.2024 10:08',
          domain: 'Kontakt',
        },
      ],
      total: 170,
    }),
  });

  const handleAddUrl = async () => {
    try {
      await apiDev.post(`knowledge-base/${agency}/${domain}/urls`, formData);
      setAddUrlModal(false);
      setFormData({ url: '' });
      refetch();
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.urlAddedSuccess'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.urlAddError'),
      });
    }
  };

  const handleRefresh = async (item: UrlItem) => {
    try {
      await apiDev.post(`knowledge-base/urls/${item.id}/refresh`);
      refetch();
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.urlRefreshed'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.refreshError'),
      });
    }
  };

  const handleDelete = async () => {
    if (!deleteModal) return;

    try {
      await apiDev.delete(`knowledge-base/urls/${deleteModal.id}`);
      setDeleteModal(null);
      refetch();
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.urlDeleteSuccess'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.deleteError'),
      });
    }
  };

  const handleViewRaw = (item: UrlItem) => {
    setEditorState('raw');
  };

  const handleViewCleaned = (item: UrlItem) => {
    setEditorState('cleaned');
  };

  const handleViewEdited = (item: UrlItem) => {
    setEditorState('edited');
  };

  const columns: ColumnDef<UrlItem>[] = [
    {
      accessorKey: 'name',
      header: t('global.name'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <a
          href={row.original.name}
          target="_blank"
          rel="noopener noreferrer"
          className="agencies__agency-cell"
          style={{ textDecoration: 'underline', color: '#005AA3' }}
        >
          {row.original.name}
        </a>
      ),
    },
    {
      accessorKey: 'domain',
      header: t('knowledgeBase.subsector'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <div style={{ minWidth: 100 }}>{row.original.domain}</div>
      ),
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <Track
          justify="end"
          align="flex-start"
          gap={32}
          style={{ width: 'max-content' }}
        >
          <Button
            appearance="text"
            className="agencies__action-btn"
            size="s"
            onClick={() => handleViewRaw(row.original)}
          >
            <Icon icon={<MdOutlineViewColumn fontSize={20} />} size="medium" />
            {t('knowledgeBase.raw')}
          </Button>
          <Button
            appearance="text"
            className="agencies__action-btn"
            size="s"
            onClick={() => handleViewCleaned(row.original)}
          >
            <Icon icon={<MdOutlineTableChart fontSize={20} />} size="medium" />
            {t('knowledgeBase.cleaned')}
          </Button>
          <Button
            appearance="text"
            className="agencies__action-btn"
            size="s"
            onClick={() => handleViewEdited(row.original)}
          >
            <Icon icon={<MdGridView fontSize={20} />} size="medium" />
            {t('knowledgeBase.edited')}
          </Button>
        </Track>
      ),
    },
    {
      id: 'excluded',
      accessorKey: 'excluded',
      enableColumnFilter: false,
      header: t('knowledgeBase.excluded'),
      cell: ({ row }) => (
        <div
          style={{
            minWidth: 100,
          }}
        >
          <SwitchBox label="" />
        </div>
      ),
    },
    {
      accessorKey: 'uploaded',
      header: t('global.uploaded'),
      enableColumnFilter: false,
    },
    {
      accessorKey: 'deleted',
      header: '',
      enableColumnFilter: false,
      enableSorting: false,
      cell: ({ row }) => (
        <Track>
          <Button
            appearance="text"
            className="agencies__action-btn"
            size="s"
            onClick={() => setDeleteModal(row.original)}
          >
            <Icon icon={<MdDeleteOutline fontSize={20} />} size="medium" />
            {t('global.delete')}
          </Button>
        </Track>
      ),
    },
  ];

  return (
    <>
      {editorState && (
        <Dialog size="fullscreen" onClose={() => setEditorState(null)}>
          <Editor
            changeEditorState={setEditorState}
            editorState={editorState}
            onCancel={() => setEditorState(null)}
            onSave={() => setEditorState(null)}
          />
        </Dialog>
      )}
      <div className="agencies">
        <Track
          style={{ marginBottom: 16, width: '100%' }}
          justify="between"
          align="center"
        >
          <div>
            <span className="agencies__agency">
              EMTA / {t('knowledgeBase.files')}
            </span>
          </div>
        </Track>

        <Card
          header={
            <Track gap={16} justify="end" align="center">
              <FormInput
                className="agencies__search"
                label={t('knowledgeBase.searchWithinListedSources')}
                name="search"
                value={formData.search}
                onChange={(e) => setFormData({ search: e.target.value })}
              />
              <Button appearance="primary">{t('global.search')}</Button>
            </Track>
          }
        >
          <div style={{ marginBottom: 16 }}>
            <FileUploader
              files={formData.files}
              onFilesChange={() => {}}
              onFileDelete={() => {}}
              maxFileSize={30 * 1024 * 1024} // 30MB
              acceptedTypes=".pdf,.doc,.docx,.txt,.html,.htm"
              multiple={true}
            />
          </div>
          <DataTable
            data={urlData?.data ?? []}
            columns={columns}
            pagination={{
              pageIndex: 0,
              pageSize: 10,
            }}
            sortable
            filterable
            pagesCount={Math.ceil((urlData?.total ?? 0) / 10)}
          />

          <div className="agencies__footer">
            <span className="agencies__total">
              {urlData?.total ?? 0} {t('knowledgeBase.results')}
            </span>
          </div>
        </Card>

        {/* Add URL Modal */}
        {addUrlModal && (
          <Dialog
            title={t('knowledgeBase.addUrlTitle')}
            onClose={() => setAddUrlModal(false)}
            footer={
              <Track gap={16} justify="end">
                <Button
                  appearance="secondary"
                  onClick={() => setAddUrlModal(false)}
                >
                  {t('global.cancel')}
                </Button>
                <Button appearance="primary" onClick={handleAddUrl}>
                  {t('knowledgeBase.addUrl')}
                </Button>
              </Track>
            }
          >
            <Track direction="vertical" gap={16}>
              <FormInput
                label={t('knowledgeBase.websiteUrl')}
                name="websiteUrl"
                type="url"
                placeholder="https://example.com"
                value={formData.url}
                onChange={(e) => setFormData({ url: e.target.value })}
              />
            </Track>
          </Dialog>
        )}

        {/* Delete Confirmation Modal */}
        {deleteModal && (
          <Dialog
            title={t('knowledgeBase.deleteFileTitle')}
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
              {t('knowledgeBase.deleteFileConfirmation', {
                file: deleteModal.name,
              })}
            </p>
          </Dialog>
        )}
      </div>
    </>
  );
};

export default KnowledgeBaseDetail;
