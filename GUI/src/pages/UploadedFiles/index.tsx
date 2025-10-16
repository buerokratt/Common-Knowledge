import { FC, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, Link } from 'react-router-dom';
import {
  MdOutlineDeleteOutline,
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
  Editor,
  Tooltip,
  FileUploader,
} from 'components';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  ColumnFiltersState,
} from '@tanstack/react-table';
import { useToast } from 'hooks/useToast';
import 'pages/Agency/AgencyList.scss';
import {
  getUploadedFiles,
  updateFileExclusion,
  deleteFile,
  updateFileEditedContentWithUpload,
  downloadFile,
  fetchFileData,
  UploadedFile,
  UploadedFilesListParams,
  EditorState,
} from 'services/files';

import {
  getSource,
  addFilesToExistingSource,
  AddFilesToExistingSourceRequest,
} from 'services/sources';
import type {
  FileItem,
  UploadProgress,
} from 'components/FileUploader/FileUploader';

interface FormData {
  search: string;
  files: FileItem[];
  subsector: string;
}

const UploadedFiles: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { id: sourceId } = useParams<{ id: string }>();

  const [deleteModal, setDeleteModal] = useState<UploadedFile | null>(null);
  const [editorState, setEditorState] = useState<EditorState | null>(null);
  const [uploadModal, setUploadModal] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<UploadProgress>({
    isUploading: false,
    currentFile: 0,
    totalFiles: 0,
    currentFileName: '',
  });
  const [formData, setFormData] = useState<FormData>({
    search: '',
    files: [],
    subsector: '',
  });
  const [searchQuery, setSearchQuery] = useState('');

  // Table state for server-side pagination and sorting
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  // Handle search input change
  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFormData((prev) => ({ ...prev, search: e.target.value }));
  };

  // Handle search button click
  const handleSearchSubmit = () => {
    setSearchQuery(formData.search);
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  };

  // Handle Enter key in search input
  const handleSearchKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      handleSearchSubmit();
    }
  };

  // Fetch source data for header
  const { data: sourceData } = useQuery({
    queryKey: ['source', sourceId],
    queryFn: () => getSource(sourceId!),
    enabled: !!sourceId,
  });

  // Convert sorting state to API format
  const getSortingParam = (sorting: SortingState): string => {
    if (sorting.length === 0) return 'last_scraped_at desc';

    const sort = sorting[0];
    let field = sort.id;

    // Map column IDs to API field names
    const fieldMap: Record<string, string> = {
      fileName: 'file_name',
      pageTitle: 'page_title',
      isExcluded: 'is_excluded',
      status: 'status',
      lastScrapedAt: 'last_scraped_at',
    };

    field = fieldMap[field] || field;
    return `${field} ${sort.desc ? 'desc' : 'asc'}`;
  };

  // API query parameters
  const queryParams: UploadedFilesListParams = useMemo(
    () => ({
      sourceId: sourceId,
      page: pagination.pageIndex + 1,
      pageSize: pagination.pageSize,
      sorting: getSortingParam(sorting),
      search: searchQuery || undefined,
    }),
    [sourceId, pagination.pageIndex, pagination.pageSize, sorting, searchQuery]
  );

  // Fetch uploaded files data
  const {
    data: uploadedFilesData,
    isLoading,
    refetch,
  } = useQuery({
    queryKey: ['uploadedFiles', queryParams],
    queryFn: () => getUploadedFiles(queryParams),
    enabled: !!sourceId,
    keepPreviousData: true,
  });

  // File upload mutation - Updated to use addFilesToExistingSource
  const uploadMutation = useMutation({
    mutationFn: async (data: AddFilesToExistingSourceRequest) => {
      // Set initial upload state
      setUploadProgress({
        isUploading: true,
        currentFile: 0,
        totalFiles: data.files.length,
        currentFileName: '',
      });

      return addFilesToExistingSource(
        data,
        (
          fileIndex: number,
          fileName: string,
          status: 'uploading' | 'success'
        ) => {
          // Update overall progress - show which file is currently being uploaded
          setUploadProgress((prev) => ({
            ...prev,
            currentFile:
              status === 'uploading' ? fileIndex + 1 : prev.currentFile,
            currentFileName:
              status === 'uploading' ? fileName : prev.currentFileName,
          }));

          // Update individual file status in real-time
          setFormData((prev) => ({
            ...prev,
            files: prev.files.map((file, index) => {
              if (index === fileIndex) {
                return {
                  ...file,
                  status: status as any,
                };
              }
              return file;
            }),
          }));
        }
      );
    },
    onMutate: () => {
      // Set all valid files to uploading status
      setFormData((prev) => ({
        ...prev,
        files: prev.files.map((file) =>
          file.status !== 'error'
            ? { ...file, status: 'uploading' as const }
            : file
        ),
      }));
    },
    onSuccess: () => {
      // Reset upload progress
      setUploadProgress({
        isUploading: false,
        currentFile: 0,
        totalFiles: 0,
        currentFileName: '',
      });

      // All files should already be marked as success from the progress callback
      setFormData((prev) => ({
        ...prev,
        files: prev.files.map((file) => ({
          ...file,
          status:
            file.status === 'uploading' ? ('success' as const) : file.status,
        })),
      }));

      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.uploadSuccess'),
      });

      // Close modal after a short delay to show success state
      setTimeout(() => {
        setUploadModal(false);
        setFormData({ search: '', files: [], subsector: '' });
      }, 1000);

      queryClient.invalidateQueries(['uploadedFiles']);
    },
    onError: (error: any) => {
      // Reset upload progress
      setUploadProgress({
        isUploading: false,
        currentFile: 0,
        totalFiles: 0,
        currentFileName: '',
      });

      // Set failed files to error status
      setFormData((prev) => ({
        ...prev,
        files: prev.files.map((file) => ({
          ...file,
          status:
            file.status === 'uploading' ? ('error' as const) : file.status,
          message: file.status === 'uploading' ? error.message : file.message,
        })),
      }));

      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.uploadError'),
      });
    },
  });

  // Delete file mutation
  const deleteMutation = useMutation({
    mutationFn: deleteFile,
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.fileDeleteSuccess'),
      });
      setDeleteModal(null);
      queryClient.invalidateQueries(['uploadedFiles']);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.deleteError'),
      });
    },
  });

  // Update exclusion mutation
  const updateExclusionMutation = useMutation({
    mutationFn: ({
      fileId,
      isExcluded,
    }: {
      fileId: string;
      isExcluded: boolean;
    }) => updateFileExclusion(fileId, isExcluded),
    onSuccess: () => {
      queryClient.invalidateQueries(['uploadedFiles']);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.updateError'),
      });
    },
  });

  // Save edited content mutation
  const saveContentMutation = useMutation({
    mutationFn: ({ content }: { content: string }) => {
      if (!editorState?.file) {
        throw new Error('No file available for saving');
      }
      setEditorState({ ...editorState, saving: true });
      const sourcePath = `${sourceData?.agencyBaseId}/${sourceData?.baseId}/${editorState.file.baseId}`;
      return updateFileEditedContentWithUpload(
        editorState.file,
        content,
        sourcePath
      );
    },
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.contentSaved'),
      });

      queryClient.invalidateQueries(['uploadedFiles']);
      setEditorState(null);
    },
    onError: (error: any) => {
      setEditorState(null);
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.saveError'),
      });
    },
  });

  const handleUpload = () => {
    if (
      !sourceData?.agencyBaseId ||
      !sourceId ||
      formData.files.length === 0 ||
      !formData.subsector
    ) {
      return;
    }

    // Extract actual File objects from FileItem[]
    const files = formData.files
      .filter((fileItem) => fileItem.status !== 'error')
      .map((fileItem) => fileItem.file);

    uploadMutation.mutate({
      agencyBaseId: sourceData.agencyBaseId,
      sourceBaseId: sourceId,
      subsector: formData.subsector,
      type: 'file',
      files,
    });
  };

  const handleDelete = () => {
    if (!deleteModal) return;
    deleteMutation.mutate(deleteModal.baseId);
  };

  const handleExclusionToggle = (file: UploadedFile, isExcluded: boolean) => {
    updateExclusionMutation.mutate({
      fileId: file.baseId,
      isExcluded,
    });
  };

  const handleViewContent = async (
    file: UploadedFile,
    type: 'raw' | 'cleaned' | 'edited'
  ) => {
    try {
      // If it's raw content, download the file instead of viewing
      if (type === 'raw') {
        await handleDownloadFile(file);
        return;
      }

      setEditorState({
        type,
        file,
        content: '',
        loading: true,
        sourceType: 'uploaded',
      });

      // For cleaned and edited content, continue with existing logic
      const path =
        type === 'cleaned' ? file.cleanedDataUrl : file.editedDataUrl;
      if (!path) return;
      const content = await fetchFileData(path);

      setEditorState({
        type,
        file,
        content,
        loading: false,
        sourceType: 'uploaded',
      });
    } catch (error) {
      setEditorState(null);
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.contentLoadError'),
      });
    }
  };

  const handleEditorStateChange = async (newState: EditorState) => {
    if (!newState.file) return;

    try {
      // If it's raw content, download the file instead of viewing
      if (newState.type === 'raw') {
        await handleDownloadFile(newState.file as UploadedFile);
        return;
      }

      setEditorState({
        ...newState,
        loading: true,
        content: '',
      });

      // Fetch content based on type
      let content = '';
      const uploadedFile = newState.file as UploadedFile;

      const editedPath =
        newState.type === 'edited'
          ? uploadedFile.editedDataUrl
          : uploadedFile.cleanedDataUrl;
      if (editedPath) {
        content = await fetchFileData(editedPath);
      }

      setEditorState({
        ...newState,
        content,
        loading: false,
      });
    } catch (error) {
      setEditorState({
        ...newState,
        loading: false,
      });
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.contentLoadError'),
      });
    }
  };

  const handleDownloadFile = async (file: UploadedFile) => {
    try {
      // Get the download URL from backend
      if (!file.originalDataUrl) return;
      await downloadFile(file.originalDataUrl, file.fileName);
    } catch (error: any) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.downloadError'),
      });
    }
  };

  const handleSaveContent = (content: string) => {
    if (!editorState) return;

    saveContentMutation.mutate({ content });
  };

  const handleFilesChange = (files: FileItem[]) => {
    setFormData((prev) => ({
      ...prev,
      files,
    }));
  };

  const handleFileDelete = (fileId: string) => {
    setFormData((prev) => ({
      ...prev,
      files: prev.files.filter((file) => file.id !== fileId),
    }));
  };

  const handlePaginationChange = (newPagination: PaginationState) => {
    setPagination(newPagination);
  };

  const handleSortingChange = (newSorting: SortingState) => {
    setSorting(newSorting);
  };

  const columns: ColumnDef<UploadedFile>[] = [
    {
      accessorKey: 'fileName',
      header: t('global.name'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <Tooltip content={row.original.fileName}>
          <div
            style={{
              maxWidth: 500,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
            className="agencies__agency-cell"
          >
            {row.original.fileName}
          </div>
        </Tooltip>
      ),
    },
    {
      accessorKey: 'subsector',
      header: t('knowledgeBase.subsector'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <Tooltip content={row.original.subsector}>
          <div
            style={{
              minWidth: 100,
              maxWidth: 120,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {row.original.subsector || '-'}
          </div>
        </Tooltip>
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
            disabled={!row.original.originalDataUrl}
            appearance="text"
            className="agencies__action-btn"
            size="s"
            onClick={() => handleViewContent(row.original, 'raw')}
          >
            <Icon icon={<MdOutlineViewColumn fontSize={20} />} size="medium" />
            {t('knowledgeBase.raw')}
          </Button>
          <Button
            disabled={
              row.original.status === 'cleaning' || !row.original.cleanedDataUrl
            }
            appearance="text"
            className="agencies__action-btn"
            size="s"
            onClick={() => handleViewContent(row.original, 'cleaned')}
          >
            <Icon icon={<MdOutlineTableChart fontSize={20} />} size="medium" />
            {t('knowledgeBase.cleaned')}
          </Button>
          <Button
            appearance="text"
            disabled={
              row.original.status === 'cleaning' || !row.original.editedDataUrl
            }
            className="agencies__action-btn"
            size="s"
            onClick={() => handleViewContent(row.original, 'edited')}
          >
            <Icon icon={<MdGridView fontSize={20} />} size="medium" />
            {t('knowledgeBase.edited')}
          </Button>
          <Button
            appearance="text"
            disabled={row.original.status === 'cleaning'}
            className="agencies__action-btn"
            size="s"
            onClick={() => setDeleteModal(row.original)}
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
    {
      accessorKey: 'excluded',
      id: 'excluded',
      enableColumnFilter: false,
      header: t('knowledgeBase.excluded'),
      cell: ({ row }) => (
        <SwitchBox
          label=""
          checked={row.original.isExcluded}
          onCheckedChange={(checked) =>
            handleExclusionToggle(row.original, checked)
          }
          disabled={
            updateExclusionMutation.isLoading ||
            row.original.status === 'cleaning'
          }
        />
      ),
    },
    {
      accessorKey: 'status',
      header: t('global.status'),
      cell: ({ row }) => {
        const color =
          row.original.status === 'finished'
            ? '#266B42'
            : row.original.status === 'cleaning'
            ? '#94690D'
            : '#AC3232';
        return (
          <span
            className={`agencies__status-cell`}
            style={{
              color: color,
              borderColor: color,
            }}
          >
            {t(`knowledgeBase.${row.original.status}`)}
          </span>
        );
      },
      enableColumnFilter: false,
    },
    {
      accessorKey: 'lastScrapedAt',
      header: t('global.uploaded'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <span>
          {new Date(row.original.createdAt).toLocaleString('et-EE', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
          })}
        </span>
      ),
    },
  ];

  if (isLoading) {
    return <div>Loading...</div>;
  }

  return (
    <>
      {editorState && (
        <Dialog size="fullscreen" onClose={() => setEditorState(null)}>
          <Editor
            changeEditorState={handleEditorStateChange}
            editorState={editorState}
            readonly={false}
            onCancel={() => setEditorState(null)}
            onSave={handleSaveContent}
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
            <Link
              to={`/agency/${sourceData?.agencyBaseId}`}
              style={{ textDecoration: 'none' }}
            >
              <span className="agencies__agency">{sourceData?.url}</span>
            </Link>
          </div>
          <Button appearance="primary" onClick={() => setUploadModal(true)}>
            {t('knowledgeBase.uploadFiles')}
          </Button>
        </Track>

        <Card
          header={
            <Track gap={16} justify="end" align="center">
              <FormInput
                className="agencies__search"
                label={t('knowledgeBase.searchWithinListedSources')}
                name="search"
                value={formData.search}
                onChange={handleSearchChange}
                onKeyPress={handleSearchKeyPress}
              />
              <Button
                appearance="primary"
                onClick={handleSearchSubmit}
                disabled={isLoading}
              >
                {t('global.search')}
              </Button>
            </Track>
          }
        >
          <DataTable
            data={uploadedFilesData?.data ?? []}
            columns={columns}
            pagination={pagination}
            setPagination={handlePaginationChange}
            sorting={sorting}
            setSorting={handleSortingChange}
            columnFilters={columnFilters}
            setFiltering={setColumnFilters}
            sortable
            filterable
            pagesCount={uploadedFilesData?.totalPages ?? 0}
            isClientSide={false}
          />

          <div className="agencies__footer">
            <span className="agencies__total">
              {uploadedFilesData?.total ?? 0} {t('knowledgeBase.results')}
            </span>
          </div>
        </Card>

        {/* Upload Modal */}
        {uploadModal && (
          <Dialog
            title={t('knowledgeBase.uploadFiles')}
            onClose={() => !uploadProgress.isUploading && setUploadModal(false)}
            footer={
              <Track gap={16} justify="end">
                <Button
                  appearance="secondary"
                  onClick={() => {
                    setUploadModal(false);
                    setFormData({
                      search: '',
                      files: [],
                      subsector: '',
                    });
                  }}
                  disabled={uploadProgress.isUploading}
                >
                  {t('global.cancel')}
                </Button>
                <Button
                  appearance="primary"
                  onClick={handleUpload}
                  disabled={
                    uploadProgress.isUploading ||
                    !formData.subsector ||
                    formData.files.length === 0 ||
                    formData.files.every((file) => file.status === 'error')
                  }
                >
                  {uploadProgress.isUploading
                    ? t('fileUpload.uploading')
                    : t('knowledgeBase.upload')}
                </Button>
              </Track>
            }
          >
            <Track direction="vertical" gap={16}>
              <FormInput
                className="url-input"
                label={t('knowledgeBase.subsector')}
                name="subsector"
                value={formData.subsector}
                onChange={(e) =>
                  setFormData((prev) => ({
                    ...prev,
                    subsector: e.target.value,
                  }))
                }
                required
                disabled={uploadProgress.isUploading}
              />
              <FileUploader
                files={formData.files}
                onFilesChange={handleFilesChange}
                onFileDelete={handleFileDelete}
                maxFileSize={30 * 1024 * 1024} // 30MB
                acceptedTypes=".pdf,.doc,.docx,.html,.htm"
                multiple={true}
                uploadProgress={uploadProgress}
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
                  disabled={deleteMutation.isLoading}
                >
                  {t('global.cancel')}
                </Button>
                <Button
                  appearance="error"
                  onClick={handleDelete}
                  disabled={deleteMutation.isLoading}
                >
                  {deleteMutation.isLoading
                    ? t('global.deleting')
                    : t('global.delete')}
                </Button>
              </Track>
            }
          >
            <p>
              {t('knowledgeBase.deleteFileConfirmation', {
                file: deleteModal.fileName,
              })}
            </p>
          </Dialog>
        )}
      </div>
    </>
  );
};

export default UploadedFiles;
